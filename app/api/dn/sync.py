"""Google Sheet synchronisation and maintenance endpoints for DN data."""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any, List

import pandas as pd
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.google_sheets import (
    create_gspread_client,
    fetch_all_values,
    fetch_cell,
    fetch_column_values,
    get_worksheet,
    list_worksheets,
    open_spreadsheet,
    update_cell_value,
)

from app.crud import (
    ensure_dn,
    get_dn_map_by_numbers,
    get_latest_dn_records_map,
    get_latest_dn_sync_log,
)
from app.db import SessionLocal, get_db
from app.dn_columns import filter_assignable_dn_fields, get_mutable_dn_columns, get_sheet_columns
from app.logging_utils import logger
from app.models import DN
from app.time_utils import TZ_GMT7, to_gmt7_iso

from ..common import normalize_dn
from .router import router
from .schemas import ArchiveMarkRequest

DN_SYNC_LOG_PATH = Path(os.getenv("DN_SYNC_LOG_PATH", "/tmp/dn_sync.log")).expanduser()
DN_SYNC_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


class _DnSyncLogFilter(logging.Filter):
    """Filter ensuring only DN sync records reach the file handler."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        return bool(getattr(record, "dn_sync", False))


_dn_sync_file_handler: logging.FileHandler | None = None


def _configure_dn_sync_logger(base_logger: logging.Logger) -> logging.LoggerAdapter:
    """Configure a DN sync logger that still propagates to the base logger."""

    global _dn_sync_file_handler

    if _dn_sync_file_handler is None or getattr(
        _dn_sync_file_handler, "baseFilename", None
    ) != str(DN_SYNC_LOG_PATH):
        handler = logging.FileHandler(DN_SYNC_LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        handler.setLevel(logging.DEBUG)
        handler.addFilter(_DnSyncLogFilter())
        base_logger.addHandler(handler)
        _dn_sync_file_handler = handler

    return logging.LoggerAdapter(base_logger, {"dn_sync": True})


dn_sync_logger = _configure_dn_sync_logger(logger)

SHEET_SYNC_INTERVAL_SECONDS = 300

def sync_delivery_status_to_sheet(
    sheet_name: str,
    row_index: int,
    dn_number: str,
    new_value: str,
) -> dict[str, Any] | None:
    """Update the delivery status in Google Sheets for the provided DN."""

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
        spreadsheet = open_spreadsheet(gc)
        worksheet = get_worksheet(spreadsheet, sheet_name)
        dn_cell_value = fetch_cell(worksheet, row_index, dn_column_position).value
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
                dn_column_values = fetch_column_values(worksheet, dn_column_position)
            except Exception as search_exc:
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
                cell = fetch_cell(worksheet, found_row_index, status_column_position)
            except Exception as fetch_exc:
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
                update_cell_value(worksheet, found_row_index, status_column_position, new_value)
            except Exception as update_exc:
                update_details["update_error"] = str(update_exc)
                return update_details

            update_details["updated"] = True
            return update_details

        try:
            cell = fetch_cell(worksheet, row_index, status_column_position)
        except Exception as fetch_exc:
            return {
                "sheet": sheet_name,
                "row": row_index,
                "column": status_column_position,
                "column_name": "status_delivery",
                "error": str(fetch_exc),
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
            update_cell_value(worksheet, row_index, status_column_position, new_value)
        except Exception as update_exc:
            update_details["update_error"] = str(update_exc)
            return update_details
        update_details["updated"] = True
        return update_details
    except Exception as exc:
        return {
            "sheet": sheet_name,
            "row": row_index,
            "column": status_column_position,
            "column_name": "status_delivery",
            "error": str(exc),
            "new_value": new_value,
        }

    return None

DEFAULT_ARCHIVE_THRESHOLD_DAYS = 7


def mark_plan_mos_rows_for_archiving(
    threshold_days: int | None = None,
) -> dict[str, Any]:
    """Mark outdated POD deliveries for archiving in Google Sheets."""

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
    spreadsheet = open_spreadsheet(gc)
    plan_sheets = fetch_plan_sheets(spreadsheet)
    sheet_titles = [sheet.title for sheet in plan_sheets]

    matched_rows = 0
    formatted_rows = 0
    affected_rows: List[dict[str, Any]] = []

    for sheet in plan_sheets:
        data = fetch_all_values(sheet)
        for idx, row in enumerate(data, start=1):
            if idx <= 3:
                continue
            if len(row) <= max(plan_mos_index, status_delivery_index):
                continue

            plan_mos_value = (row[plan_mos_index] or "").strip()
            status_delivery_value = (row[status_delivery_index] or "").strip().upper()

            if not plan_mos_value or status_delivery_value != "POD":
                continue

            parsed_date = parse_date(plan_mos_value)
            if isinstance(parsed_date, datetime):
                row_date = parsed_date.date()
            else:
                continue

            if row_date <= threshold_date:
                matched_rows += 1
                cell_value = fetch_cell(sheet, idx, status_delivery_index + 1).value
                normalized_cell_value = (cell_value or "").strip().upper()
                if normalized_cell_value != "ARCHIVED":
                    update_cell_value(sheet, idx, status_delivery_index + 1, "ARCHIVED")
                    formatted_rows += 1
                    affected_rows.append(
                        {
                            "sheet": sheet.title,
                            "row": idx,
                            "plan_mos_date": plan_mos_value,
                            "status_delivery": status_delivery_value,
                            "new_status_delivery": "ARCHIVED",
                        }
                    )

    return {
        "ok": True,
        "sheets": sheet_titles,
        "matched_rows": matched_rows,
        "updated_rows": formatted_rows,
        "affected_rows": affected_rows,
    }


MONTH_MAP = {
    "JAN": "JAN",
    "FEB": "FEB",
    "MAR": "MAR",
    "APR": "APR",
    "MEI": "MAY",
    "MAY": "MAY",
    "JUN": "JUN",
    "JUL": "JUL",
    "AGU": "AUG",
    "AUG": "AUG",
    "SEP": "SEP",
    "OKT": "OCT",
    "OCT": "OCT",
    "NOV": "NOV",
    "DES": "DEC",
    "DEC": "DEC",
}

DATE_FORMATS = [
    "%d %b %y",
    "%d %b %Y",
    "%d%b%y",
    "%d%b%Y",
    "%d/%m/%y",
    "%d/%m/%Y",
    "%d-%m-%y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%d %B %Y",
    "%d %B %y",
    "%Y/%m/%d",
]


def fetch_plan_sheets(spreadsheet):
    """获取以 'Plan MOS' 开头的所有工作表"""
    start = perf_counter()
    sheets = list(list_worksheets(spreadsheet))
    dn_sync_logger.debug(
        "Fetched %d worksheets in %.3fs",
        len(sheets),
        perf_counter() - start,
    )
    dn_sync_logger.debug("Spreadsheet has %d worksheets", len(sheets))
    plan_sheets = [sheet for sheet in sheets if sheet.title.startswith("Plan MOS")]
    dn_sync_logger.debug(
        "Filtered plan sheets in %.3fs", perf_counter() - start
    )
    if plan_sheets:
        sheet_titles = [sheet.title for sheet in plan_sheets]
        preview_titles = ", ".join(sheet_titles[:3])
        if len(sheet_titles) > 3:
            preview_titles = f"{preview_titles}, ..."
        dn_sync_logger.info(
            "Found %d 'Plan MOS' sheets to sync (%s)",
            len(plan_sheets),
            preview_titles,
        )
    else:
        dn_sync_logger.info("No 'Plan MOS' sheets available for syncing")
    dn_sync_logger.debug(
        "Filtered %d plan sheets: %s",
        len(plan_sheets),
        [sheet.title for sheet in plan_sheets],
    )
    return plan_sheets


@lru_cache(maxsize=2048)
def parse_date(date_str: str):
    """解析日期字符串"""
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

def normalize_database_fields(db: Session) -> None:
    """规范数据库中需要标准化的字段。"""

    dn_sync_logger.debug("Starting database field normalization")

    dn_entries = db.query(DN).filter(DN.plan_mos_date.isnot(None)).all()
    normalized_plan_dates = 0

    for entry in dn_entries:
        raw_value = entry.plan_mos_date.strip() if entry.plan_mos_date else None
        if not raw_value:
            continue

        parsed_value = parse_date(raw_value)
        if isinstance(parsed_value, datetime):
            normalized_value = parsed_value.strftime("%d %b %y")
            if normalized_value != entry.plan_mos_date:
                entry.plan_mos_date = normalized_value
                normalized_plan_dates += 1

    status_entries = (
        db.query(DN)
        .filter(
            or_(
                DN.status_delivery.is_(None),
                func.trim(DN.status_delivery) == "",
            )
        )
        .all()
    )
    normalized_status_delivery = 0

    for entry in status_entries:
        entry.status_delivery = "No Status"
        normalized_status_delivery += 1

    if normalized_plan_dates or normalized_status_delivery:
        db.commit()

    if normalized_plan_dates:
        dn_sync_logger.info(
            "Normalized plan_mos_date for %d DN rows", normalized_plan_dates
        )
    else:
        dn_sync_logger.debug("No plan_mos_date values required normalization")

    if normalized_status_delivery:
        dn_sync_logger.info(
            "Normalized status_delivery for %d DN rows", normalized_status_delivery
        )
    else:
        dn_sync_logger.debug("No status_delivery values required normalization")


def process_sheet_data(sheet, columns: List[str]) -> pd.DataFrame:
    """处理工作表数据"""
    fetch_start = perf_counter()
    all_values = fetch_all_values(sheet)
    dn_sync_logger.debug(
        "Processing sheet '%s' with %d total rows", sheet.title, len(all_values)
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
    dn_sync_logger.debug(
        "Sheet '%s' produced DataFrame with %d rows", sheet.title, len(df)
    )
    dn_sync_logger.debug(
        "Processed sheet '%s' into DataFrame in %.3fs",
        sheet.title,
        perf_counter() - fetch_start,
    )
    return df


def process_all_sheets(sh) -> pd.DataFrame:
    """处理所有符合条件的工作表并合并数据"""
    total_start = perf_counter()
    plan_sheets = fetch_plan_sheets(sh)
    columns_start = perf_counter()
    columns = get_sheet_columns()
    dn_sync_logger.debug(
        "Loaded sheet column definitions in %.3fs",
        perf_counter() - columns_start,
    )
    processing_start = perf_counter()
    all_data = [process_sheet_data(sheet, columns) for sheet in plan_sheets]
    dn_sync_logger.debug(
        "Processed %d plan sheets in %.3fs",
        len(plan_sheets),
        perf_counter() - processing_start,
    )
    if not all_data:
        dn_sync_logger.info("No plan sheets found to process; returning empty DataFrame")
        return pd.DataFrame(columns=columns)
    combined = pd.concat(all_data, ignore_index=True)
    dn_sync_logger.info(
        "Combined sheet data into DataFrame with %d rows", len(combined)
    )
    dn_sync_logger.debug(
        "Combined DataFrame has %d rows and %d columns",
        len(combined),
        len(combined.columns),
    )
    dn_sync_logger.debug(
        "Completed sheet processing workflow in %.3fs",
        perf_counter() - total_start,
    )
    return combined


def normalize_sheet_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if pd.isna(value):
        return None
    return value


@dataclass
class DnSyncResult:
    """Aggregate information about a DN sheet synchronisation run."""

    synced_numbers: List[str]
    created_count: int
    updated_count: int
    ignored_count: int


def _values_match(existing_value: Any, new_value: Any) -> bool:
    """Return True if the database value already matches the incoming value."""

    if existing_value is None and new_value is None:
        return True

    if isinstance(existing_value, str):
        existing_value = existing_value.strip()
        if not existing_value:
            existing_value = None

    if isinstance(new_value, str):
        new_value = new_value.strip()
        if not new_value:
            new_value = None

    return existing_value == new_value


def sync_dn_sheet_to_db(db: Session) -> DnSyncResult:
    """同步 Google Sheet 中的 DN 数据到数据库。"""

    start_time = datetime.utcnow()
    dn_sync_logger.info("Starting sync_dn_sheet_to_db run")

    try:
        dn_sync_logger.debug("Creating gspread client")
        client_start = perf_counter()
        gc = create_gspread_client()
        dn_sync_logger.debug(
            "Created gspread client in %.3fs", perf_counter() - client_start
        )
        dn_sync_logger.debug("Opening spreadsheet using shared helper")
        open_start = perf_counter()
        sh = open_spreadsheet(gc)
        dn_sync_logger.debug(
            "Spreadsheet opened successfully in %.3fs",
            perf_counter() - open_start,
        )
        sheet_start = perf_counter()
        combined_df = process_all_sheets(sh)
        dn_sync_logger.debug(
            "Fetched and combined sheet data in %.3fs",
            perf_counter() - sheet_start,
        )
    except Exception as exc:
        logger.exception("Failed to fetch DN sheet data: %s", exc)
        dn_sync_logger.exception("Failed to fetch DN sheet data")
        raise

    sheet_columns: List[str] = list(combined_df.columns)
    records: List[dict[str, Any]] = []
    dn_numbers: set[str] = set()

    total_rows = len(combined_df) if not combined_df.empty else 0
    skipped_missing_number = 0
    skipped_empty_payload = 0
    dn_sync_logger.info("Preparing to process %d sheet rows", total_rows)
    dn_sync_logger.debug("DataFrame contains %d total rows", total_rows)

    processing_start = perf_counter()

    row_normalization_total = 0.0
    plan_mos_parse_total = 0.0
    plan_mos_parse_count = 0
    dn_normalization_total = 0.0
    record_build_total = 0.0
    rows_iterated = 0
    rows_persisted = 0

    if not combined_df.empty:
        columns_tuple = tuple(sheet_columns)
        try:
            dn_index = sheet_columns.index("dn_number")
        except ValueError:
            dn_sync_logger.warning("Sheet data missing 'dn_number' column; skipping processing")
            dn_index = None

        plan_mos_index = None
        if "plan_mos_date" in sheet_columns:
            plan_mos_index = sheet_columns.index("plan_mos_date")

        if dn_index is not None:
            for row_values in combined_df.itertuples(index=False, name=None):
                rows_iterated += 1
                row_normalization_start = perf_counter()
                normalized_row: list[Any] = []
                has_payload = False

                for idx, raw_value in enumerate(row_values):
                    normalized_value = normalize_sheet_value(raw_value)
                    if (
                        plan_mos_index is not None
                        and idx == plan_mos_index
                        and isinstance(normalized_value, str)
                        and normalized_value
                    ):
                        parse_start = perf_counter()
                        parsed_plan_mos_date = parse_date(normalized_value)
                        parse_duration = perf_counter() - parse_start
                        plan_mos_parse_total += parse_duration
                        plan_mos_parse_count += 1
                        if isinstance(parsed_plan_mos_date, datetime):
                            normalized_value = parsed_plan_mos_date.strftime("%d %b %y")

                    if idx == dn_index:
                        normalization_start = perf_counter()
                        normalized_dn = normalize_dn(str(normalized_value or ""))
                        dn_normalization_total += perf_counter() - normalization_start
                        if not normalized_dn:
                            skipped_missing_number += 1
                            normalized_row = []
                            break
                        dn_numbers.add(normalized_dn)
                        normalized_row.append(normalized_dn)
                        has_payload = True
                        continue

                    normalized_row.append(normalized_value)
                    if normalized_value not in (None, ""):
                        has_payload = True

                row_normalization_total += perf_counter() - row_normalization_start

                if not normalized_row:
                    continue

                if not has_payload:
                    skipped_empty_payload += 1
                    continue

                record = dict(zip(columns_tuple, normalized_row))
                records.append(record)
                rows_persisted += 1

    dn_sync_logger.debug(
        "Row processing complete: iterated=%d, persisted=%d", rows_iterated, rows_persisted
    )

    if not records:
        dn_sync_logger.info(
            "Combined DataFrame is empty; no rows to process"
        )
        return DnSyncResult([], 0, 0, 0)

    dn_sync_logger.info(
        "Prepared %d candidate DN records after normalization", len(records)
    )

    dn_sync_logger.debug(
        "Fetching existing DN map for %d numbers", len(dn_numbers)
    )
    existing_dn_map = get_dn_map_by_numbers(db, dn_numbers)
    dn_sync_logger.debug(
        "Loaded existing DN map with %d entries", len(existing_dn_map)
    )

    mutable_columns = get_mutable_dn_columns()
    dn_sync_logger.debug("Mutable DN columns: %s", mutable_columns)

    latest_records_map = get_latest_dn_records_map(db, list(dn_numbers)) if dn_numbers else {}
    dn_sync_logger.debug("Fetched %d latest DN records", len(latest_records_map))

    create_payload_by_number: dict[str, dict[str, Any]] = {}
    update_payload_by_number: dict[str, dict[str, Any]] = {}
    numbers_to_create: set[str] = set()
    numbers_to_update: set[str] = set()
    numbers_unchanged: set[str] = set()
    created_columns: set[str] = set()
    updated_columns: set[str] = set()
    created_field_total = 0
    updated_field_total = 0

    latest_merge_total = 0.0
    assignable_filter_total = 0.0
    non_null_filter_total = 0.0
    change_detection_total = 0.0
    payload_mutation_total = 0.0

    for record in records:
        number = record.get("dn_number")
        if not number:
            continue

        sheet_fields = dict(record)
        latest = latest_records_map.get(number)

        if latest:
            merge_start = perf_counter()
            sheet_fields = {
                **sheet_fields,
                "status": latest.status,
                "remark": latest.remark,
                "photo_url": latest.photo_url,
                "lng": latest.lng,
                "lat": latest.lat,
            }
            latest_merge_total += perf_counter() - merge_start
        elif number not in numbers_to_create:
            dn_sync_logger.debug("Preparing creation for DN %s from sheet data", number)

        assignable_start = perf_counter()
        assignable_fields = filter_assignable_dn_fields(
            sheet_fields, mutable_columns
        )
        assignable_filter_total += perf_counter() - assignable_start

        non_null_start = perf_counter()
        non_null_fields = {
            key: value for key, value in assignable_fields.items() if value is not None
        }
        non_null_filter_total += perf_counter() - non_null_start
        if not non_null_fields:
            continue

        comparison_start = perf_counter()
        existing_dn = existing_dn_map.get(number)
        if existing_dn:
            changed_fields: dict[str, Any] = {}
            for key, value in non_null_fields.items():
                if not _values_match(getattr(existing_dn, key, None), value):
                    changed_fields[key] = value
            change_detection_total += perf_counter() - comparison_start
            if not changed_fields:
                numbers_unchanged.add(number)
                continue

            if number not in numbers_to_update:
                dn_sync_logger.debug(
                    "Preparing update for existing DN %s after detecting differences", number
                )
            numbers_to_update.add(number)
            updated_columns.update(changed_fields.keys())
            payload = update_payload_by_number.setdefault(
                number, {"id": existing_dn.id, "dn_number": number}
            )
            mutation_start = perf_counter()
            payload.update(changed_fields)
            payload_mutation_total += perf_counter() - mutation_start
            updated_field_total += len(changed_fields)
        else:
            change_detection_total += perf_counter() - comparison_start
            numbers_to_create.add(number)
            created_columns.update(non_null_fields.keys())
            payload = create_payload_by_number.setdefault(
                number, {"dn_number": number}
            )
            mutation_start = perf_counter()
            payload.update(non_null_fields)
            payload_mutation_total += perf_counter() - mutation_start
            created_field_total += len(non_null_fields)

    if rows_iterated:
        dn_sync_logger.info(
            (
                "Row processing timing: total_normalization=%.3fs (avg %.6fs over %d rows), "
                "plan_mos_parse=%.3fs (%d calls), dn_normalization=%.3fs (avg %.6fs), "
                "record_build=%.3fs (avg %.6fs over %d persisted)"
            ),
            row_normalization_total,
            row_normalization_total / rows_iterated,
            rows_iterated,
            plan_mos_parse_total,
            plan_mos_parse_count,
            dn_normalization_total,
            dn_normalization_total / rows_iterated,
            record_build_total,
            (record_build_total / rows_persisted) if rows_persisted else 0.0,
            rows_persisted,
        )

    if records:
        dn_sync_logger.info(
            (
                "Payload preparation timing: latest_merge=%.3fs, "
                "assignable_filter=%.3fs (avg %.6fs), non_null_filter=%.3fs (avg %.6fs), "
                "change_detection=%.3fs (avg %.6fs), payload_mutation=%.3fs (avg %.6fs)"
            ),
            latest_merge_total,
            assignable_filter_total,
            assignable_filter_total / len(records),
            non_null_filter_total,
            non_null_filter_total / len(records),
            change_detection_total,
            change_detection_total / len(records),
            payload_mutation_total,
            payload_mutation_total / len(records),
        )

    processing_duration = perf_counter() - processing_start
    total_payloads = len(create_payload_by_number) + len(update_payload_by_number)
    dn_sync_logger.debug(
        "Prepared %d DN payloads (create=%d, update=%d) in %.3fs",
        total_payloads,
        len(numbers_to_create),
        len(numbers_to_update),
        processing_duration,
    )
    unchanged_count = len(numbers_unchanged)
    dn_sync_logger.info(
        (
            "DN payload summary: create=%d (fields=%d, columns=%d), "
            "update=%d (fields=%d, columns=%d), unchanged=%d (processing_time=%.3fs)"
        ),
        len(numbers_to_create),
        created_field_total,
        len(created_columns),
        len(numbers_to_update),
        updated_field_total,
        len(updated_columns),
        unchanged_count,
        processing_duration,
    )

    create_payloads = list(create_payload_by_number.values())
    update_payloads = list(update_payload_by_number.values())
    created_count = len(create_payloads)
    updated_count = len(update_payloads)

    if create_payloads or update_payloads:
        db_start = perf_counter()

        if create_payloads:
            insert_stmt = insert(DN).on_conflict_do_nothing(index_elements=[DN.dn_number])
            db.execute(insert_stmt, create_payloads)

        if update_payloads:
            db.bulk_update_mappings(DN, update_payloads)

        db.commit()
        dn_sync_logger.debug(
            "Persisted %d new and %d updated DN entries in %.3fs",
            created_count,
            updated_count,
            perf_counter() - db_start,
        )
        dn_sync_logger.info(
            "Applied DN changes: created=%d, updated=%d",
            created_count,
            updated_count,
        )
    else:
        dn_sync_logger.info(
            "No DN sheet changes detected; skipping database write",
        )

    normalization_start = perf_counter()
    normalize_database_fields(db)
    dn_sync_logger.debug(
        "normalize_database_fields completed in %.3fs",
        perf_counter() - normalization_start,
    )

    dn_sync_logger.info(
        (
            "Completed sync_dn_sheet_to_db run: processed_rows=%d, valid_records=%d, "
            "unique_dns=%d, skipped_missing=%d, skipped_empty=%d, updated=%d, "
            "ignored=%d, duration=%.3fs"
        ),
        total_rows,
        len(records),
        len(dn_numbers),
        skipped_missing_number,
        skipped_empty_payload,
        updated_count,
        unchanged_count,
        (datetime.utcnow() - start_time).total_seconds(),
    )

    return DnSyncResult(
        synced_numbers=sorted(dn_numbers),
        created_count=created_count,
        updated_count=updated_count,
        ignored_count=unchanged_count,
    )

def _sync_dn_sheet_with_new_session() -> DnSyncResult:
    db = SessionLocal()
    try:
        return sync_dn_sheet_to_db(db)
    finally:
        db.close()



async def run_dn_sheet_sync_once() -> DnSyncResult:
    return await asyncio.to_thread(_sync_dn_sheet_with_new_session)


async def scheduled_dn_sheet_sync() -> None:
    try:
        result = await run_dn_sheet_sync_once()
        if result.synced_numbers:
            logger.info(
                "Synced %d DN numbers from Google Sheet (created=%d, updated=%d, ignored=%d)",
                len(result.synced_numbers),
                result.created_count,
                result.updated_count,
                result.ignored_count,
            )
    except Exception:
        logger.exception("Scheduled DN sheet sync failed")


@router.post("/archive/mark")
def mark_archive_rows(request: ArchiveMarkRequest) -> dict[str, Any]:
    try:
        result = mark_plan_mos_rows_for_archiving(request.threshold_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return result


@router.post("/sync")
def trigger_dn_sync():
    try:
        result = _sync_dn_sheet_with_new_session()
    except Exception:
        logger.exception("Manual DN sheet sync failed")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": "dn_sync_failed",
                "errorInfo": traceback.format_exc(),
            },
        )

    return {
        "ok": True,
        "synced_count": len(result.synced_numbers),
        "created_count": result.created_count,
        "updated_count": result.updated_count,
        "ignored_count": result.ignored_count,
        "dn_numbers": result.synced_numbers,
    }


@router.get("/sync/log/latest")
def get_latest_dn_sync_log_entry(db: Session = Depends(get_db)):
    log_entry = get_latest_dn_sync_log(db)
    if not log_entry:
        return {"ok": True, "data": None}

    return {
        "ok": True,
        "data": {
            "id": log_entry.id,
            "status": log_entry.status,
            "synced_count": log_entry.synced_count,
            "dn_numbers": log_entry.dn_numbers,
            "message": log_entry.message,
            "error_message": log_entry.error_message,
            "error_traceback": log_entry.error_traceback,
            "created_at": to_gmt7_iso(log_entry.created_at),
        },
    }


@router.get("/sync/log/file")
def download_dn_sync_log():
    if _dn_sync_file_handler is not None:
        flush = getattr(_dn_sync_file_handler, "flush", None)
        if callable(flush):
            flush()

    if not DN_SYNC_LOG_PATH.exists():
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "log_file_not_found"},
        )

    return FileResponse(
        path=DN_SYNC_LOG_PATH,
        filename=DN_SYNC_LOG_PATH.name,
        media_type="text/plain",
    )


@router.get("/stats/{date}")
async def get_dn_stats(date: str):
    gc = create_gspread_client()
    sh = open_spreadsheet(gc)

    combined_df = process_all_sheets(sh)

    if combined_df.empty:
        return {
            "ok": True,
            "data": {
                "total_dn": 0,
                "status_breakdown": {},
                "plan_mos_breakdown": {},
                "delivery_status_breakdown": {},
            },
        }

    combined_df["plan_mos_date"] = combined_df["plan_mos_date"].apply(parse_date)
    combined_df["plan_mos_date"] = combined_df["plan_mos_date"].apply(
        lambda x: x.date() if isinstance(x, datetime) else x
    )

    combined_df["status_delivery"] = combined_df["status_delivery"].apply(
        lambda x: (x or "").strip().upper()
    )

    target_date = datetime.strptime(date, "%Y-%m-%d").date()
    filtered_df = combined_df[combined_df["plan_mos_date"] == target_date]

    status_breakdown = filtered_df["status"].value_counts().to_dict()
    plan_mos_breakdown = (
        combined_df["plan_mos_date"].value_counts().sort_index().to_dict()
    )
    delivery_status_breakdown = (
        filtered_df["status_delivery"].value_counts().to_dict()
    )

    return {
        "ok": True,
        "data": {
            "total_dn": len(filtered_df),
            "status_breakdown": status_breakdown,
            "plan_mos_breakdown": plan_mos_breakdown,
            "delivery_status_breakdown": delivery_status_breakdown,
        },
    }


__all__ = [
    "SHEET_SYNC_INTERVAL_SECONDS",
    "create_gspread_client",
    "download_dn_sync_log",
    "get_dn_stats",
    "get_latest_dn_sync_log_entry",
    "mark_archive_rows",
    "mark_plan_mos_rows_for_archiving",
    "run_dn_sheet_sync_once",
    "scheduled_dn_sheet_sync",
    "sync_delivery_status_to_sheet",
    "sync_dn_sheet_to_db",
    "trigger_dn_sync",
]
