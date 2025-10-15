"""Google Sheet data processing utilities."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from time import perf_counter
from typing import Any, List

import gspread.utils
import pandas as pd

from app.core.google import SPREADSHEET_URL, create_gspread_client
from app import state
from app.dn_columns import get_sheet_columns
from app.utils.logging import dn_sync_logger, logger
from app.utils.string import normalize_dn
from app.utils.time import TZ_GMT7

MONTH_MAP = {"Sept": "Sep", "Okt": "Oct"}
DATE_FORMATS = [
    "%d %b %y",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d%b",
    "%Y/%m/%d",
]
ARCHIVE_TEXT_COLOR = {"red": 0.6, "green": 0.6, "blue": 0.6}
DEFAULT_ARCHIVE_THRESHOLD_DAYS = 7
NOTE_TEXT = "Modified by Fast Tracker"
NOTE_LINK_URI = "https://idnsc.dpdns.org/admin"

__all__ = [
    "parse_date",
    "fetch_plan_sheets",
    "process_sheet_data",
    "process_all_sheets",
    "normalize_sheet_value",
    "sync_dn_record_to_sheet",
    "mark_plan_mos_rows_for_archiving",
    "ARCHIVE_TEXT_COLOR",
    "DEFAULT_ARCHIVE_THRESHOLD_DAYS",
]


@lru_cache(maxsize=2048)
def parse_date(date_str: str):
    """Parse a date string returning datetime if format matches."""
    if date_str is None:
        return None
    if isinstance(date_str, datetime):
        return date_str
    if not isinstance(date_str, str):
        return date_str

    normalized = date_str
    for incorrect, correct in MONTH_MAP.items():
        normalized = normalized.replace(incorrect, correct)
    trimmed = normalized.strip()

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(trimmed, fmt)
        except ValueError:
            continue

    return normalized


def fetch_plan_sheets(spreadsheet) -> list:
    """Fetch worksheets whose title starts with 'Plan MOS'."""
    start = perf_counter()
    sheets = spreadsheet.worksheets()
    dn_sync_logger.debug("Fetched %d worksheets in %.3fs", len(sheets), perf_counter() - start)
    plan_sheets = [sheet for sheet in sheets if sheet.title.startswith("Plan MOS")]
    if plan_sheets:
        titles = [sheet.title for sheet in plan_sheets]
        preview = ", ".join(titles[:3]) + (", ..." if len(titles) > 3 else "")
        dn_sync_logger.info("Found %d 'Plan MOS' sheets to sync (%s)", len(plan_sheets), preview)
    else:
        dn_sync_logger.info("No 'Plan MOS' sheets available for syncing")
    dn_sync_logger.debug("Filtered %d plan sheets: %s", len(plan_sheets), [s.title for s in plan_sheets])
    return plan_sheets


def process_sheet_data(sheet, columns: List[str]) -> pd.DataFrame:
    """Read sheet values and align columns."""
    fetch_start = perf_counter()
    all_values = sheet.get_all_values()
    dn_sync_logger.debug(
        "sheet.get_all_values for '%s' returned %d rows in %.3fs",
        sheet.title,
        len(all_values),
        perf_counter() - fetch_start,
    )
    data = all_values[3:]
    trimmed: List[List[str]] = []
    row_numbers: List[int] = []
    column_count = len(columns)

    for index, row in enumerate(data, start=4):
        row_values = row[:column_count]
        if len(row_values) < column_count:
            row_values = row_values + [""] * (column_count - len(row_values))
        trimmed.append(row_values)
        row_numbers.append(index)

    df = pd.DataFrame(trimmed, columns=columns)
    df["gs_sheet"] = sheet.title
    df["gs_row"] = row_numbers
    dn_sync_logger.debug("Sheet '%s' produced DataFrame with %d rows", sheet.title, len(df))
    return df


def process_all_sheets(sh) -> pd.DataFrame:
    """Combine all plan sheets into a single DataFrame."""
    total_start = perf_counter()
    plan_sheets = fetch_plan_sheets(sh)
    # Update runtime mapping of sheet title -> id whenever we enumerate worksheets
    try:
        state.update_gs_map_from_sheets(plan_sheets)
        dn_sync_logger.debug("Updated gs_sheet_name_to_id_map with %d sheets", len(plan_sheets))
    except Exception:
        dn_sync_logger.exception("Failed to update gs_sheet_name_to_id_map")
    columns = get_sheet_columns()
    all_data = [process_sheet_data(sheet, columns) for sheet in plan_sheets]
    if not all_data:
        dn_sync_logger.info("No plan sheets found to process; returning empty DataFrame")
        return pd.DataFrame(columns=columns)
    combined = pd.concat(all_data, ignore_index=True)
    dn_sync_logger.info("Combined sheet data into DataFrame with %d rows", len(combined))
    dn_sync_logger.debug("Completed sheet processing in %.3fs", perf_counter() - total_start)
    return combined


def normalize_sheet_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if pd.isna(value):
        return None
    return value


def sync_dn_record_to_sheet(
    sheet_name: str,
    row_index: int,
    dn_number: str,
    status_delivery: str | None = None,
    status_site: str | None = None,
    remark: str | None = None,
    updated_by: str | None = None,
) -> dict[str, Any]:
    """一次性写入 status_delivery、status_site、remark、atd/ata 到 Google Sheet。"""
    from app.constants import ARRIVAL_STATUSES, DEPARTURE_STATUSES

    column_names = get_sheet_columns()
    result: dict[str, Any] = {}
    try:
        def _add_note_and_format(worksheet, a1_address: str, note_text: str | None = None, link_uri: str | None = None) -> None:
            """Insert a note and apply formatting (fontSize=8 and optional link) to a cell.

            This helper swallows exceptions and logs failures at debug level.
            """
            try:
                if note_text:
                    worksheet.insert_note(a1_address, note_text)
                fmt: dict[str, Any] = {"textFormat": {"fontSize": 8}}
                if link_uri:
                    # nest link under textFormat if requested (gspread accepts this structure)
                    fmt["textFormat"]["link"] = {"uri": link_uri}
                worksheet.format(a1_address, fmt)
            except Exception:
                dn_sync_logger.debug("Failed to add note/format to cell %s", a1_address)

        gc = create_gspread_client()
        sh = gc.open_by_url(SPREADSHEET_URL)
        # When we open the spreadsheet for an update, refresh the sheet name->id mapping
        try:
            state.update_gs_map_from_sheets(sh.worksheets())
        except Exception:
            dn_sync_logger.debug("Failed to refresh gs_sheet_name_to_id_map during sync_dn_record_to_sheet")
        worksheet = sh.worksheet(sheet_name)
        dn_column_position = column_names.index("dn_number") + 1
        status_delivery_column_position = column_names.index("status_delivery") + 1
        status_site_column_position = None
        if "status_site" in column_names:
            status_site_column_position = column_names.index("status_site") + 1
        issue_remark_column_position = None
        if "issue_remark" in column_names:
            issue_remark_column_position = column_names.index("issue_remark") + 1
        atd_column_position = None
        ata_column_position = None
        if "actual_depart_from_start_point_atd" in column_names:
            atd_column_position = column_names.index("actual_depart_from_start_point_atd") + 1
        if "actual_arrive_time_ata" in column_names:
            ata_column_position = column_names.index("actual_arrive_time_ata") + 1

        # 校验 DN 行
        found_cell_value = False
        try:
            dn_cell_value = worksheet.cell(row_index, dn_column_position).value
            normalized_sheet_dn = normalize_dn(dn_cell_value or "")
        except Exception:
            found_cell_value = False

        if not found_cell_value or normalized_sheet_dn != dn_number:
            # 查找正确行
            dn_column_values = worksheet.col_values(dn_column_position)
            found_matches = [
                idx for idx, value in enumerate(dn_column_values, start=1) if normalize_dn(value or "") == dn_number
            ]
            found_row_index = found_matches[-1] if found_matches else None
            if found_row_index is None:
                result["error"] = "dn_number not found in sheet"
                return result
            row_index = found_row_index
            result["row_corrected"] = row_index

        # Collect repeatCell requests for batch update (value + note + formatting)
        batch_requests: List[dict[str, Any]] = []

        def _add_repeat_cell_request(col_pos: int, value: str) -> None:
            # col_pos is 1-based column index
            start_row = row_index - 1
            start_col = col_pos - 1
            batch_requests.append(
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startRowIndex": start_row,
                            "endRowIndex": start_row + 1,
                            "startColumnIndex": start_col,
                            "endColumnIndex": start_col + 1,
                        },
                        "cell": {
                            "userEnteredValue": {"stringValue": str(value)},
                            "note": NOTE_TEXT,
                            "userEnteredFormat": {"textFormat": {"fontSize": 8, "link": {"uri": NOTE_LINK_URI}}},
                        },
                        "fields": "userEnteredValue,note,userEnteredFormat.textFormat",
                    }
                }
            )

        # Prepare values to write
        if status_delivery is not None:
            _add_repeat_cell_request(status_delivery_column_position, status_delivery)
            result["status_delivery_updated"] = True
        if status_site_column_position is not None and status_site is not None:
            _add_repeat_cell_request(status_site_column_position, status_site)
            result["status_site_updated"] = True
        if issue_remark_column_position is not None and remark is not None:
            _add_repeat_cell_request(issue_remark_column_position, remark)
            result["issue_remark_updated"] = True

        # 写 atd/ata
        status_delivery_upper = (status_delivery or "").strip().upper()
        now_gmt7 = datetime.now(TZ_GMT7)
        timestamp_str = f"{now_gmt7.month}/{now_gmt7.day}/{now_gmt7.year} {now_gmt7.hour}:{now_gmt7.minute:02d}:{now_gmt7.second:02d}"
        if status_delivery_upper in ARRIVAL_STATUSES and ata_column_position is not None:
            _add_repeat_cell_request(ata_column_position, timestamp_str)
            result["actual_arrive_time_ata_updated"] = True
        if status_delivery_upper in DEPARTURE_STATUSES and atd_column_position is not None:
            _add_repeat_cell_request(atd_column_position, timestamp_str)
            result["actual_depart_from_start_point_atd_updated"] = True

        # 添加 note 和 hyperlink 到 status_delivery cell
        # Execute batch update (single request containing all repeatCell requests)
        if batch_requests:
            try:
                sh.batch_update({"requests": batch_requests})
            except Exception as bexc:
                # fallback: try to write individually if batch fails
                dn_sync_logger.exception("Batch update failed, falling back to per-cell updates: %s", bexc)
                for req in batch_requests:
                    try:
                        r = req.get("repeatCell")
                        rng = r.get("range")
                        cell = r.get("cell")
                        # convert range to a1
                        r0 = rng.get("startRowIndex") + 1
                        c0 = rng.get("startColumnIndex") + 1
                        a1 = gspread.utils.rowcol_to_a1(r0, c0)
                        # write value if present
                        val = None
                        if cell and cell.get("userEnteredValue"):
                            val = cell.get("userEnteredValue").get("stringValue")
                        if val is not None:
                            worksheet.update_cell(r0, c0, val)
                        # add note & format
                        _add_note_and_format(worksheet, a1, note_text=NOTE_TEXT, link_uri=NOTE_LINK_URI)
                    except Exception:
                        dn_sync_logger.exception("Fallback per-cell write failed for request: %s", req)

        result["updated"] = True
        result["row"] = row_index
        result["sheet"] = sheet_name
        result["dn_number"] = dn_number
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def mark_plan_mos_rows_for_archiving(threshold_days: int | None = None) -> dict[str, Any]:
    """Mark rows for archiving where plan_mos_date is older than threshold and status_delivery is POD."""
    if threshold_days is None:
        threshold_days = DEFAULT_ARCHIVE_THRESHOLD_DAYS
    if threshold_days < 0:
        raise ValueError("threshold_days must be non-negative")

    column_names = get_sheet_columns()
    try:
        plan_mos_index = column_names.index("plan_mos_date")
    except ValueError as exc:
        raise RuntimeError("plan_mos_date column not found in sheet definition") from exc
    try:
        status_delivery_index = column_names.index("status_delivery")
    except ValueError as exc:
        raise RuntimeError("status_delivery column not found in sheet definition") from exc

    threshold_date = (datetime.now(TZ_GMT7) - timedelta(days=threshold_days)).date()
    logger.info(
        "Marking rows for archiving where plan_mos_date is before %s and status_delivery is POD",
        threshold_date.isoformat(),
    )

    gc = create_gspread_client()
    sh = gc.open_by_url(SPREADSHEET_URL)
    plan_sheets = fetch_plan_sheets(sh)
    # keep the in-memory sheet name -> id mapping up-to-date
    try:
        state.update_gs_map_from_sheets(plan_sheets)
    except Exception:
        dn_sync_logger.debug("Failed to update gs_sheet_name_to_id_map during mark_plan_mos_rows_for_archiving")
    sheet_titles = [sheet.title for sheet in plan_sheets]

    matched_rows = 0
    formatted_rows = 0
    affected_rows: List[dict[str, Any]] = []
    pending_requests: List[dict[str, Any]] = []

    def flush_requests() -> None:
        if not pending_requests:
            return
        sh.batch_update({"requests": list(pending_requests)})
        pending_requests.clear()

    for sheet in plan_sheets:
        values = sheet.get_all_values()
        if len(values) <= 3:
            continue

        sheet_column_count = getattr(sheet, "col_count", None) or max((len(row) for row in values), default=0)
        effective_color_range_end = max(sheet_column_count, 0)

        for row_offset, row_values in enumerate(values[3:], start=4):
            if not row_values:
                continue
            if not any((cell or "").strip() for cell in row_values):
                continue

            plan_cell = row_values[plan_mos_index] if len(row_values) > plan_mos_index else ""
            status_cell = row_values[status_delivery_index] if len(row_values) > status_delivery_index else ""
            if not plan_cell:
                continue

            parsed_plan = parse_date(plan_cell)
            plan_date_value: date | None = None
            if isinstance(parsed_plan, datetime):
                plan_date_value = parsed_plan.date()
            else:
                try:
                    pandas_date = pd.to_datetime(plan_cell, errors="coerce")
                except Exception:
                    pandas_date = None
                if pandas_date is not None and not pd.isna(pandas_date):
                    plan_date_value = pandas_date.date()

            if plan_date_value is None or plan_date_value >= threshold_date:
                continue

            if (status_cell or "").strip().upper() != "POD":
                continue

            matched_rows += 1
            row_number = row_offset
            row_start_index = row_number - 1

            entry: dict[str, Any] = {
                "sheet": sheet.title,
                "row": row_number,
                "plan_mos_date": plan_cell,
                "status_delivery": status_cell,
            }
            if effective_color_range_end > 0:
                pending_requests.append(
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet.id,
                                "startRowIndex": row_start_index,
                                "endRowIndex": row_start_index + 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": effective_color_range_end,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "textFormat": {
                                        "foregroundColor": ARCHIVE_TEXT_COLOR,
                                        "fontSize": 8,
                                        "link": {"uri": NOTE_LINK_URI},
                                    }
                                }
                            },
                            "fields": "userEnteredFormat.textFormat.foregroundColor,userEnteredFormat.textFormat.fontSize,userEnteredFormat.textFormat.link",
                        }
                    }
                )
                formatted_rows += 1
                entry["formatted"] = True
            else:
                entry["formatted"] = False
                entry["formatting_skipped"] = True
            affected_rows.append(entry)

            if len(pending_requests) >= 90:
                flush_requests()

    flush_requests()

    logger.info("Matched %d rows for archiving criteria; formatted %d rows", matched_rows, formatted_rows)

    return {
        "threshold_days": threshold_days,
        "threshold_date": threshold_date.isoformat(),
        "matched_rows": matched_rows,
        "formatted_rows": formatted_rows,
        "sheets_processed": sheet_titles,
        "affected_rows": affected_rows,
    }
