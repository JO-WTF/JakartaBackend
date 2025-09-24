"""处理 DN 与 Google Sheet 同步的核心服务。"""

from __future__ import annotations

import asyncio
import logging
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, List

import gspread
import pandas as pd
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..crud import UnitOfWork
from ..db import SessionLocal
from ..models import DN
from ..settings import settings
from ..utils.normalization import normalize_dn, normalize_sheet_value, parse_date

_DN_SYNC_LOG_PATH = Path(os.getenv("DN_SYNC_LOG_PATH", "/tmp/dn_sync.log")).expanduser()
_DN_SYNC_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_dn_sync_logger = logging.getLogger("dn_sync")

if not any(
    isinstance(handler, logging.FileHandler)
    and getattr(handler, "baseFilename", None) == str(_DN_SYNC_LOG_PATH)
    for handler in _dn_sync_logger.handlers
):
    file_handler = logging.FileHandler(_DN_SYNC_LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    file_handler.setLevel(logging.DEBUG)
    _dn_sync_logger.addHandler(file_handler)

if not any(isinstance(handler, logging.StreamHandler) for handler in _dn_sync_logger.handlers):
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    stream_handler.setLevel(logging.DEBUG)
    _dn_sync_logger.addHandler(stream_handler)

_dn_sync_logger.setLevel(logging.DEBUG)
_dn_sync_logger.propagate = False


def get_dn_sync_logger() -> logging.Logger:
    """返回在同步任务之间共享的日志记录器。"""

    return _dn_sync_logger


def get_dn_sync_log_path() -> Path:
    """提供同步日志文件的绝对路径，便于排查问题。"""

    return _DN_SYNC_LOG_PATH


def get_gspread_client() -> gspread.Client:
    """根据配置的 API Key 初始化 gspread 客户端。"""

    api_key = settings.google_api_key
    if not api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY configuration")
    _dn_sync_logger.debug("Creating gspread client with configured API key")
    return gspread.api_key(api_key)


def get_spreadsheet_url() -> str:
    """读取配置中的 Google Sheet 地址，缺失时抛出异常。"""

    url = settings.google_sheet_url
    if not url:
        raise RuntimeError("Missing GOOGLE_SHEET_URL configuration")
    return url


def get_sheet_sync_interval_seconds() -> int:
    """返回周期性同步任务的时间间隔（秒）。"""

    return settings.dn_sheet_sync_interval_seconds


def fetch_plan_sheets(sheet_url) -> list:
    """筛选标题以 ``Plan MOS`` 开头的工作表，作为 DN 数据来源。"""

    sheets = sheet_url.worksheets()
    _dn_sync_logger.debug("Spreadsheet has %d worksheets", len(sheets))
    plan_sheets = [sheet for sheet in sheets if sheet.title.startswith("Plan MOS")]
    if plan_sheets:
        sheet_titles = [sheet.title for sheet in plan_sheets]
        preview_titles = ", ".join(sheet_titles[:3])
        if len(sheet_titles) > 3:
            preview_titles = f"{preview_titles}, ..."
        _dn_sync_logger.info(
            "Found %d 'Plan MOS' sheets to sync (%s)",
            len(plan_sheets),
            preview_titles,
        )
    else:
        _dn_sync_logger.info("No 'Plan MOS' sheets available for syncing")
    return plan_sheets


def process_sheet_data(sheet, columns: List[str]) -> pd.DataFrame:
    """将单个工作表裁剪为指定列并转为 DataFrame。"""

    all_values = sheet.get_all_values()
    _dn_sync_logger.debug(
        "Processing sheet '%s' with %d total rows", sheet.title, len(all_values)
    )
    data = all_values[3:]
    trimmed: List[List[str]] = []
    column_count = len(columns)
    for row in data:
        row_values = row[:column_count]
        if len(row_values) < column_count:
            row_values = row_values + [""] * (column_count - len(row_values))
        trimmed.append(row_values)
    df = pd.DataFrame(trimmed, columns=columns)
    _dn_sync_logger.debug(
        "Sheet '%s' produced DataFrame with %d rows", sheet.title, len(df)
    )
    return df


def process_all_sheets(sheet) -> pd.DataFrame:
    """汇总所有符合条件的工作表，生成统一的 DataFrame。"""

    plan_sheets = fetch_plan_sheets(sheet)
    from ..dn_columns import get_sheet_columns

    columns = get_sheet_columns()
    all_data = [process_sheet_data(plan_sheet, columns) for plan_sheet in plan_sheets]
    if not all_data:
        _dn_sync_logger.info("No plan sheets found to process; returning empty DataFrame")
        return pd.DataFrame(columns=columns)
    combined = pd.concat(all_data, ignore_index=True)
    _dn_sync_logger.info(
        "Combined sheet data into DataFrame with %d rows", len(combined)
    )
    return combined


def normalize_database_fields(session: Session) -> None:
    """在同步完成后清洗数据库中常见的字段异常。"""

    from sqlalchemy import func, or_
    from ..models import DN as DNModel

    _dn_sync_logger.debug("Starting database field normalization")

    dn_entries = session.query(DNModel).filter(DNModel.plan_mos_date.isnot(None)).all()
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
        session.query(DNModel)
        .filter(
            or_(
                DNModel.status_delivery.is_(None),
                func.trim(DNModel.status_delivery) == "",
            )
        )
        .all()
    )
    normalized_status_delivery = 0
    for entry in status_entries:
        entry.status_delivery = "No Status"
        normalized_status_delivery += 1

    if normalized_plan_dates or normalized_status_delivery:
        session.flush()


def sync_dn_sheet_to_db(session: Session, *, logger_obj: logging.Logger | None = None) -> List[str]:
    """将 Google Sheet 中的内容写入数据库并返回成功的 DN 列表。"""

    log = logger_obj or _dn_sync_logger
    start_time = datetime.utcnow()
    log.info("Starting sync_dn_sheet_to_db run")

    try:
        gc = get_gspread_client()
        spreadsheet_url = get_spreadsheet_url()
        _dn_sync_logger.debug("Opening spreadsheet URL: %s", spreadsheet_url)
        sh = gc.open_by_url(spreadsheet_url)
        _dn_sync_logger.debug("Spreadsheet opened successfully")
        combined_df = process_all_sheets(sh)
    except Exception as exc:  # pragma: no cover - 依赖外部服务可能失败
        if log:
            log.exception("Failed to fetch DN sheet data: %s", exc)
        _dn_sync_logger.exception("Failed to fetch DN sheet data")
        raise

    sheet_columns: List[str] = list(combined_df.columns)
    records: List[dict[str, Any]] = []
    dn_numbers: set[str] = set()

    total_rows = len(combined_df) if not combined_df.empty else 0
    skipped_missing_number = 0
    skipped_empty_payload = 0
    _dn_sync_logger.info("Preparing to process %d sheet rows", total_rows)

    if not combined_df.empty:
        for record in combined_df.to_dict(orient="records"):
            cleaned = {key: normalize_sheet_value(value) for key, value in record.items()}
            plan_mos_date_value = cleaned.get("plan_mos_date")
            if isinstance(plan_mos_date_value, str) and plan_mos_date_value:
                parsed_plan_mos_date = parse_date(plan_mos_date_value)
                if isinstance(parsed_plan_mos_date, datetime):
                    cleaned["plan_mos_date"] = parsed_plan_mos_date.strftime("%d %b %y")
            raw_number = cleaned.get("dn_number")
            raw_number_str = str(raw_number).strip() if raw_number is not None else ""
            normalized_number = normalize_dn(raw_number_str) if raw_number_str else ""
            if not normalized_number:
                skipped_missing_number += 1
                continue
            cleaned["dn_number"] = normalized_number
            if all(value is None for key, value in cleaned.items() if key != "dn_number"):
                skipped_empty_payload += 1
                continue
            records.append(cleaned)
            dn_numbers.add(normalized_number)
    else:
        _dn_sync_logger.info("Combined DataFrame is empty; no rows to process")

    if not dn_numbers:
        _dn_sync_logger.info(
            "No DN numbers extracted (skipped_missing=%d, skipped_empty=%d)",
            skipped_missing_number,
            skipped_empty_payload,
        )
        return []

    created_count = 0
    updated_count = 0
    with UnitOfWork(session) as uow:
        latest_records_for_update = uow.dn.get_latest_records_map(dn_numbers)
        payload_by_number: dict[str, dict[str, Any]] = {}
        bulk_update_columns: set[str] = set()
        numbers_to_create: set[str] = set()
        numbers_to_update: set[str] = set()

        from ..dn_columns import filter_assignable_dn_fields

        for entry in records:
            number = entry["dn_number"]
            sheet_fields = {key: entry.get(key) for key in sheet_columns if key != "dn_number"}
            latest = latest_records_for_update.get(number)
            if latest:
                if not sheet_fields.get("du_id") and latest.du_id:
                    sheet_fields["du_id"] = latest.du_id
                sheet_fields.update(
                    {
                        "status": latest.status,
                        "remark": latest.remark,
                        "photo_url": latest.photo_url,
                        "lng": latest.lng,
                        "lat": latest.lat,
                    }
                )
                numbers_to_update.add(number)
            else:
                numbers_to_create.add(number)

            assignable_fields = filter_assignable_dn_fields(sheet_fields)
            non_null_fields = {
                key: value for key, value in assignable_fields.items() if value is not None
            }
            if non_null_fields:
                bulk_update_columns.update(non_null_fields.keys())
            payload = payload_by_number.setdefault(number, {"dn_number": number})
            payload.update(non_null_fields)

        if payload_by_number:
            insert_stmt = insert(DN)
            update_mappings = {
                column: insert_stmt.excluded[column]
                for column in sorted(bulk_update_columns)
            }
            upsert_stmt = insert_stmt.on_conflict_do_update(
                index_elements=[DN.dn_number],
                set_=update_mappings,
            )
            uow.session.execute(upsert_stmt, list(payload_by_number.values()))

        normalize_database_fields(uow.session)
        created_count = len(numbers_to_create)
        updated_count = len(numbers_to_update)

    _dn_sync_logger.info(
        "Completed sync_dn_sheet_to_db run: processed_rows=%d, valid_records=%d, unique_dns=%d, "
        "skipped_missing=%d, skipped_empty=%d, created=%d, updated=%d, duration=%.3fs",
        total_rows,
        len(records),
        len(dn_numbers),
        skipped_missing_number,
        skipped_empty_payload,
        created_count,
        updated_count,
        (datetime.utcnow() - start_time).total_seconds(),
    )

    return sorted(dn_numbers)


def sync_with_new_session() -> List[str]:
    """打开新的数据库会话并执行一次同步流程。"""

    session = SessionLocal()
    try:
        return sync_dn_sheet_to_db(session, logger_obj=_dn_sync_logger)
    finally:
        session.close()


async def run_dn_sheet_sync_once() -> List[str]:
    """在异步环境中通过线程执行同步任务。"""

    return await asyncio.to_thread(sync_with_new_session)


def perform_sync_with_logging() -> List[str]:
    """执行同步并记录 DNSyncLog，便于审计与追踪。"""

    session = SessionLocal()
    try:
        try:
            numbers = sync_dn_sheet_to_db(session, logger_obj=_dn_sync_logger)
        except Exception as exc:  # pragma: no cover - 外部依赖调用失败
            with UnitOfWork(session) as uow:
                uow.sync_logs.create(
                    status="failed",
                    synced_numbers=None,
                    message="Failed to sync DN data from Google Sheet",
                    error_message=str(exc),
                    error_traceback=traceback.format_exc(),
                )
            raise
        else:
            with UnitOfWork(session) as uow:
                message = (
                    "Synced %d DN numbers from Google Sheet" % len(numbers)
                    if numbers
                    else "Google Sheet returned no DN rows to sync"
                )
                uow.sync_logs.create(
                    status="success",
                    synced_numbers=numbers,
                    message=message,
                )
            return numbers
    finally:
        session.close()
