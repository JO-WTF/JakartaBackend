"""FastAPI application entrypoint."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import router as api_router
from app.core.aging_orders import scheduled_aging_orders_sheet_sync
from app.core.sync import scheduled_dn_sheet_sync
from app.core.status_delivery_summary import (
    scheduled_status_delivery_lsp_summary_capture,
)
from app.api.dn.archive import scheduled_archive
from app.utils.time import TZ_GMT7
from app.db import Base, engine, SessionLocal
from app import models  # noqa: F401 - ensure models are imported for metadata creation
from app.db_migrations import run_startup_migrations, prepare_dn_table_migration
from app.dn_columns import refresh_dynamic_columns
from app.settings import settings
from app.utils.logging import logger

app = FastAPI(title="DN Backend API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.storage_driver != "s3":
    os.makedirs(settings.storage_disk_path, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=settings.storage_disk_path, check_dir=False), name="uploads")

Base.metadata.create_all(bind=engine)

# Prepare DN table for migration (handle old schema)
with SessionLocal() as db:
    prepare_dn_table_migration(db)

# Run startup migrations to ensure schema is up to date
with SessionLocal() as db:
    run_startup_migrations(db)

refresh_dynamic_columns(engine)

app.include_router(api_router)

_scheduler: AsyncIOScheduler | None = None
_SHEET_SYNC_JOB_ID = "dn_sheet_sync"
_LSP_SUMMARY_JOB_ID = "status_delivery_lsp_summary"
_AGING_ORDERS_SYNC_JOB_ID = "aging_orders_sheet_sync"
SHEET_SYNC_INTERVAL_SECONDS = 300
AGING_ORDERS_SYNC_INTERVAL_SECONDS = 60


@app.exception_handler(Exception)
async def all_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse(status_code=500, content={"ok": False, "error": "internal_error"})


@app.on_event("startup")
async def _start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        scheduled_dn_sheet_sync,
        trigger=IntervalTrigger(seconds=SHEET_SYNC_INTERVAL_SECONDS),
        id=_SHEET_SYNC_JOB_ID,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.utcnow() + timedelta(seconds=5),
    )
    _scheduler.add_job(
        scheduled_aging_orders_sheet_sync,
        trigger=IntervalTrigger(seconds=AGING_ORDERS_SYNC_INTERVAL_SECONDS),
        id=_AGING_ORDERS_SYNC_JOB_ID,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.utcnow() + timedelta(seconds=5),
    )
    _scheduler.add_job(
        scheduled_status_delivery_lsp_summary_capture,
        trigger=CronTrigger(minute=0),
        id=_LSP_SUMMARY_JOB_ID,
        max_instances=1,
        coalesce=True,
    )
    # Schedule daily archive at 04:00 Jakarta time (GMT+7)
    # _scheduler.add_job(
    #     scheduled_archive,
    #     trigger=CronTrigger(hour=4, minute=0, timezone=TZ_GMT7),
    #     id="scheduled_archive",
    #     max_instances=1,
    #     coalesce=True,
    # )
    _scheduler.start()


@app.on_event("shutdown")
async def _shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=10000, reload=True)
