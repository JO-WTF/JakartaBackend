"""DN archiving endpoints."""

from fastapi import APIRouter, Body, HTTPException

from app.core.sheet import DEFAULT_ARCHIVE_THRESHOLD_DAYS, mark_plan_mos_rows_for_archiving
from app.schemas.dn import ArchiveMarkRequest
from app.utils.logging import logger

router = APIRouter(prefix="/api/dn/archive")


@router.post("/mark")
def mark_archive_rows_api(
    payload: ArchiveMarkRequest | None = Body(None, description="Optional configuration for Plan MOS archiving."),
):
    threshold_days = payload.threshold_days if payload is not None else DEFAULT_ARCHIVE_THRESHOLD_DAYS

    try:
        result = mark_plan_mos_rows_for_archiving(threshold_days=threshold_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # pragma: no cover - gspread/network failures
        logger.exception("Failed to mark rows for archiving: %s", exc)
        raise HTTPException(status_code=500, detail="failed_to_mark_archive_rows")

    return {"ok": True, "data": result}
