"""Google Sheet data processing utilities."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from time import perf_counter
from typing import Any, List

import pandas as pd

from app.core.google import SPREADSHEET_URL, create_gspread_client
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

__all__ = [
    "parse_date",
    "fetch_plan_sheets",
    "process_sheet_data",
    "process_all_sheets",
    "normalize_sheet_value",
    "sync_delivery_status_to_sheet",
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


def sync_delivery_status_to_sheet(
    sheet_name: str,
    row_index: int,
    dn_number: str,
    new_value: str,
) -> dict[str, Any] | None:
    """Write delivery status back to Google Sheet for a DN entry."""
    column_names = get_sheet_columns()

    try:
        status_column_position = column_names.index("status_delivery") + 1
    except ValueError:
        return {
            "sheet": sheet_name,
            "row": row_index,
            "column_name": "status_delivery",
            "error": "status_delivery column not found in sheet definition",
            "new_value": new_value,
        }

    try:
        dn_column_position = column_names.index("dn_number") + 1
    except ValueError:
        return {
            "sheet": sheet_name,
            "row": row_index,
            "column_name": "dn_number",
            "error": "dn_number column not found in sheet definition",
            "new_value": new_value,
        }

    try:
        gc = create_gspread_client()
        sh = gc.open_by_url(SPREADSHEET_URL)
        worksheet = sh.worksheet(sheet_name)
        dn_cell_value = worksheet.cell(row_index, dn_column_position).value
        normalized_sheet_dn = normalize_dn(dn_cell_value or "")

        if normalized_sheet_dn != dn_number:
            search_details: dict[str, Any] = {
                "sheet": sheet_name,
                "row": row_index,
                "column": status_column_position,
                "column_name": "status_delivery",
                "current_dn_number": dn_cell_value,
                "expected_dn_number": dn_number,
                "error": "dn_number mismatch for target row",
                "new_value": new_value,
            }
            try:
                dn_column_values = worksheet.col_values(dn_column_position)
            except Exception as search_exc:  # pragma: no cover - gspread errors
                search_details["search_error"] = str(search_exc)
                return search_details

            found_row_index: int | None = None
            for idx, value in enumerate(dn_column_values, start=1):
                if normalize_dn(value or "") == dn_number:
                    found_row_index = idx
                    break

            if found_row_index is None:
                search_details["search_result"] = "dn_number not found in sheet"
                return search_details

            try:
                cell = worksheet.cell(found_row_index, status_column_position)
            except Exception as fetch_exc:  # pragma: no cover - gspread errors
                search_details["search_row"] = found_row_index
                search_details["search_error"] = str(fetch_exc)
                return search_details

            update_details = {
                "sheet": sheet_name,
                "row": found_row_index,
                "column": status_column_position,
                "column_name": "status_delivery",
                "current_value": cell.value,
                "new_value": new_value,
                "found_via_search": True,
                "original_row_mismatch": row_index,
            }

            normalized_current_value = (cell.value or "").strip()
            if normalized_current_value == new_value:
                update_details["skipped"] = True
                update_details["normalized_current_value"] = normalized_current_value
                return update_details

            try:
                worksheet.update_cell(found_row_index, status_column_position, new_value)
            except Exception as update_exc:  # pragma: no cover - gspread errors
                update_details["update_error"] = str(update_exc)
                return update_details

            update_details["updated"] = True
            return update_details

        cell = worksheet.cell(row_index, status_column_position)
    except Exception as exc:  # pragma: no cover - gspread errors
        return {
            "sheet": sheet_name,
            "row": row_index,
            "column": status_column_position,
            "column_name": "status_delivery",
            "error": str(exc),
            "new_value": new_value,
        }

    update_details = {
        "sheet": sheet_name,
        "row": row_index,
        "column": status_column_position,
        "column_name": "status_delivery",
        "current_value": cell.value,
        "new_value": new_value,
    }

    normalized_current_value = (cell.value or "").strip()
    if normalized_current_value == new_value:
        update_details["skipped"] = True
        update_details["normalized_current_value"] = normalized_current_value
        return update_details

    try:
        worksheet.update_cell(row_index, status_column_position, new_value)
    except Exception as update_exc:  # pragma: no cover - gspread errors
        update_details["update_error"] = str(update_exc)
        return update_details

    update_details["updated"] = True
    return update_details


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
                            "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": ARCHIVE_TEXT_COLOR}}},
                            "fields": "userEnteredFormat.textFormat.foregroundColor",
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
