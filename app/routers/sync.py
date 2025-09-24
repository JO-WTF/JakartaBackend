"""提供 DN 同步与日志相关接口的路由。"""
from __future__ import annotations

import asyncio
import traceback
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..crud import UnitOfWork
from ..db import get_db
from ..schemas import (
    DNStatsResponse,
    DNStatsRow,
    DNSyncLogEntry,
    DNSyncLogResponse,
    DNSyncResponse,
)
from ..services.dn_sync import (
    get_gspread_client,
    get_dn_sync_log_path,
    get_dn_sync_logger,
    perform_sync_with_logging,
    process_all_sheets,
    get_spreadsheet_url,
)
from ..utils.normalization import parse_date

router = APIRouter(prefix="/api/dn", tags=["DN Sync"])


@router.post("/sync", response_model=DNSyncResponse)
async def trigger_dn_sync() -> DNSyncResponse:
    """手动触发 DN 与 Google Sheet 的同步流程。"""

    logger = get_dn_sync_logger()
    try:
        numbers = await asyncio.to_thread(perform_sync_with_logging)
    except Exception:  # pragma: no cover - 外部依赖调用失败
        logger.exception("Manual DN sheet sync failed")
        return DNSyncResponse(
            ok=False,
            error="dn_sync_failed",
            errorInfo=traceback.format_exc(),
        )

    return DNSyncResponse(ok=True, synced_count=len(numbers), dn_numbers=numbers)


@router.get("/sync/log/latest", response_model=DNSyncLogResponse)
def get_latest_dn_sync_log_entry(db: Session = Depends(get_db)) -> DNSyncLogResponse:
    """查询最新的同步日志条目。"""

    with UnitOfWork(db) as uow:
        log_entry = uow.sync_logs.get_latest()
    if not log_entry:
        return DNSyncLogResponse(ok=True, data=None)
    return DNSyncLogResponse(
        ok=True,
        data=DNSyncLogEntry.model_validate(log_entry),
    )


@router.get("/sync/log/file")
def download_dn_sync_log():
    """下载完整的同步日志文件。"""

    logger = get_dn_sync_logger()
    for handler in logger.handlers:
        flush = getattr(handler, "flush", None)
        if callable(flush):
            flush()

    log_path = get_dn_sync_log_path()
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="log_file_not_found")

    return FileResponse(path=log_path, filename=log_path.name, media_type="text/plain")


@router.get("/stats/{date}", response_model=DNStatsResponse)
def get_dn_stats(
    date: str = Path(..., description="Plan MOS date in '%d %b %y' format"),
) -> DNStatsResponse:
    """根据 Plan MOS 日期汇总 Google Sheet 中的 DN 统计数据。"""

    logger = get_dn_sync_logger()
    try:
        gc = get_gspread_client()
        sh = gc.open_by_url(get_spreadsheet_url())
        combined_df = process_all_sheets(sh)
    except Exception as exc:  # pragma: no cover - 外部依赖调用失败
        logger.exception("Failed to fetch DN stats from sheet: %s", exc)
        raise HTTPException(status_code=500, detail="dn_stats_fetch_failed")

    combined_df["plan_mos_date"] = combined_df["plan_mos_date"].apply(
        lambda x: parse_date(x).strftime("%d-%b-%y") if parse_date(x) else x
    )
    day_df = combined_df[combined_df["plan_mos_date"] == date]
    day_df["status_delivery"] = day_df["status_delivery"].apply(
        lambda x: x.upper() if x else "NO STATUS"
    )

    pivot_df = (
        day_df.groupby(["plan_mos_date", "region", "status_delivery"])["dn_number"]
        .nunique()
        .unstack(fill_value=0)
    )

    all_statuses = [
        "PREPARE VEHICLE",
        "ON THE WAY",
        "ON SITE",
        "POD",
        "REPLAN MOS PROJECT",
        "WAITING PIC FEEDBACK",
        "REPLAN MOS DUE TO LSP DELAY",
        "CLOSE BY RN",
        "CANCEL MOS",
        "NO STATUS",
    ]
    extra = list(set(pivot_df.columns.tolist()) - set(all_statuses))
    final_statuses = all_statuses + extra
    pivot_df = pivot_df.reindex(columns=final_statuses, fill_value=0)
    pivot_df["Total"] = pivot_df.sum(axis=1)

    table_df = pivot_df.reset_index()
    table_df.columns = ["date", "group"] + table_df.columns.to_list()[2:]

    rows: List[DNStatsRow] = []
    for _, row in table_df.iterrows():
        rows.append(
            DNStatsRow(
                group=row["group"],
                date=row["date"],
                values=list(row)[2:],
            )
        )

    return DNStatsResponse(ok=True, data=rows)
