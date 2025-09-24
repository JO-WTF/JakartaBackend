"""管理 DN 表同步定时任务的调度器。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..services.dn_sync import (
    get_dn_sync_logger,
    get_sheet_sync_interval_seconds,
    perform_sync_with_logging,
)

_scheduler: AsyncIOScheduler | None = None
_JOB_ID = "dn_sheet_sync"


async def _scheduled_dn_sheet_sync() -> None:
    """执行 DN 表同步任务，并记录异常信息。"""

    logger = get_dn_sync_logger()
    try:
        numbers = await asyncio.to_thread(perform_sync_with_logging)
        if numbers:
            logger.info("Synced %d DN numbers from Google Sheet", len(numbers))
    except Exception:  # pragma: no cover - 外部依赖调用失败
        logger.exception("Scheduled DN sheet sync failed")


def start_scheduler() -> None:
    """在调度器未运行时启动后台任务。"""

    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _scheduled_dn_sheet_sync,
        trigger=IntervalTrigger(seconds=get_sheet_sync_interval_seconds()),
        id=_JOB_ID,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.utcnow() + timedelta(seconds=5),
    )
    _scheduler.start()


def shutdown_scheduler() -> None:
    """关闭后台调度器并释放资源。"""

    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
