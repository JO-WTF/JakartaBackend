"""DN sync endpoints."""

from __future__ import annotations

import traceback

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.sync import sync_dn_sheet_with_new_session
from app.crud import get_latest_dn_sync_log
from app.db import get_db
from app.utils.logging import DN_SYNC_LOG_PATH, flush_dn_sync_log, logger

router = APIRouter(prefix="/api/dn")


@router.post("/sync")
def trigger_dn_sync():
    try:
        result = sync_dn_sheet_with_new_session()
    except Exception:
        logger.exception("Manual DN sheet sync failed")
        return JSONResponse(status_code=500, content={"ok": False, "error": "dn_sync_failed", "errorInfo": traceback.format_exc()})

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

    from app.utils.time import to_gmt7_iso  # local import to avoid circular

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
    flush_dn_sync_log()
    if not DN_SYNC_LOG_PATH.exists():
        return JSONResponse(status_code=404, content={"ok": False, "error": "log_file_not_found"})
    return FileResponse(path=DN_SYNC_LOG_PATH, filename=DN_SYNC_LOG_PATH.name, media_type="text/plain")
