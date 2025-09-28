"""Application entry point configuring FastAPI and shared services."""

from __future__ import annotations

import os
import traceback
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import register_routes
from .api.dn import SHEET_SYNC_INTERVAL_SECONDS, scheduled_dn_sheet_sync
from .db import Base, engine
from .dn_columns import refresh_dynamic_columns
from .logging_utils import logger
from .settings import settings

app = FastAPI(title="DU Backend API", version="1.1.0")

_scheduler: AsyncIOScheduler | None = None
_SHEET_SYNC_JOB_ID = "dn_sheet_sync"


@app.exception_handler(Exception)
async def all_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled error on %s %s\n%s",
        request.method,
        request.url.path,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "internal_error",
            "errorInfo": traceback.format_exc(),
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(settings.storage_disk_path, exist_ok=True)
if settings.storage_driver != "s3":
    app.mount(
        "/uploads",
        StaticFiles(directory=settings.storage_disk_path, check_dir=False),
        name="uploads",
    )

Base.metadata.create_all(bind=engine)
refresh_dynamic_columns(engine)

register_routes(app)


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
    _scheduler.start()


@app.on_event("shutdown")
async def _shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


# Optional entry-point for local development via ``python -m app.main``
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=10000, reload=True)
