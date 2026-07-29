"""DN update and mutation endpoints."""

from __future__ import annotations

from typing import Any, List, Optional
import json
from datetime import date, datetime, time, timezone
from app.utils.logging import logger

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.constants import (
    DN_RE,
    ARRIVAL_STATUSES,
    DEPARTURE_STATUSES,
)
from app.crud import (
    add_dn_record,
    delete_dn,
    delete_dn_record,
    ensure_dn,
    get_existing_dn_numbers,
    _ACTIVE_DN_EXPR,
)
from app.db import get_db, SessionLocal
from app.models import CheckResult, DN
from app.services.dn_checkins import DNCheckinError, create_dn_checkin
from app.storage import save_file
from app.utils.string import normalize_dn
from app.utils.time import TZ_GMT7
from app.core.sheet import sync_dn_record_to_sheet

router = APIRouter(prefix="/api/dn")


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _json_loads_or_default(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _check_result_created_date_range(value: date | None) -> tuple[datetime, datetime]:
    local_date = value or datetime.now(TZ_GMT7).date()
    start = datetime.combine(local_date, time(0, 0, 0), tzinfo=TZ_GMT7)
    end = datetime.combine(local_date, time(23, 59, 59, 999_999), tzinfo=TZ_GMT7)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _serialize_check_result(record: CheckResult, *, include_details: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": record.id,
        "report_id": record.report_id,
        "dn_number": record.dn_number,
        "lsp": record.lsp,
        "checker_name": record.checker_name,
        "check_time": record.check_time,
        "status": record.status,
        "box_count": record.box_count,
        "checked_count": record.checked_count,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }
    if include_details:
        data["boxes"] = _json_loads_or_default(record.boxes_json, [])
        data["metadata"] = _json_loads_or_default(record.metadata_json, {})
    return data


def _current_timestamp_gmt7() -> str:
    now = datetime.now(TZ_GMT7)
    return f"{now.month}/{now.day}/{now.year} {now.hour}:{now.minute:02d}:{now.second:02d}"


def _format_log_entries(entries: dict[str, Any]) -> str:
    return "; ".join(f"{key} = {value!r}" for key, value in entries.items()) + ";"


async def _run_post_update_tasks(
    *,
    dn_number: str,
    status_delivery: str | None,
    status_site: str | None,
    remark: str | None,
    updated_by_value: str | None,
    phone_number_value: str | None,
    gs_sheet_name: str | None,
    gs_row_index: Optional[int],
    dn_row_id: Optional[int],
    checkin_payload: dict[str, Any] | None,
) -> None:
    should_check_sheet = (
        gs_sheet_name and isinstance(gs_row_index, int) and gs_row_index > 0 and status_delivery is not None
    )

    if should_check_sheet:
        try:
            gpread_result = sync_dn_record_to_sheet(
                gs_sheet_name,  # type: ignore[arg-type]
                gs_row_index,  # type: ignore[arg-type]
                dn_number,
                status_delivery,
                status_site,
                remark,
                updated_by_value,
                phone_number_value,
            )
            logger.info("Google Sheet update result: %s", json.dumps(gpread_result))
            corrected_row: Optional[int] = None
            if isinstance(gpread_result, dict):
                if isinstance(gpread_result.get("row_corrected"), int):
                    corrected_row = gpread_result["row_corrected"]
                elif isinstance(gpread_result.get("row"), int):
                    corrected_row = gpread_result["row"]

            if corrected_row is not None and dn_row_id is not None:
                with SessionLocal() as bg_db:
                    dn_row = bg_db.query(DN).filter(DN.id == dn_row_id).one_or_none()
                    if dn_row is not None:
                        if getattr(dn_row, "gs_row", None) != corrected_row:
                            dn_row.gs_row = corrected_row
                        if gs_sheet_name and getattr(dn_row, "gs_sheet", None) != gs_sheet_name:
                            dn_row.gs_sheet = gs_sheet_name
                        bg_db.add(dn_row)
                        bg_db.commit()
        except Exception:
            logger.exception("Failed to sync DN record to Google Sheet", extra={"dn_number": dn_number})

    if checkin_payload:
        try:
            await create_dn_checkin(checkin_payload)
        except DNCheckinError:
            logger.exception("Failed to sync DN update to check-in service", extra={"dn_number": dn_number})


@router.post("/update")
async def update_dn(
    background_tasks: BackgroundTasks,
    dnNumber: str = Form(...),
    status: str | None = Form(None, description="legacy 状态字段，可选"),
    status_delivery: str | None = Form(None, description="配送状态，可选"),
    status_site: str | None = Form(None, description="站点状态，可选"),
    remark: str | None = Form(None),
    photo: UploadFile | None = File(None),
    lng: str | float | None = Form(None),
    lat: str | float | None = Form(None),
    updated_by: str | None = Form(None),
    phone_number: str | None = Form(None),
    db: Session = Depends(get_db),
):
    dn_number = normalize_dn(dnNumber)
    if not DN_RE.fullmatch(dn_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    photo_metadata: dict[str, Any] | None = None
    if photo is not None:
        photo_metadata = {
            "filename": getattr(photo, "filename", None),
            "content_type": getattr(photo, "content_type", None),
            "has_content": bool(photo.filename),
        }

    payload_entries: list[tuple[str, Any]] = []

    def append_if_present(key: str, value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            if value.strip() == "":
                return
        payload_entries.append((key, value))

    append_if_present("dn_number.raw", dnNumber)
    append_if_present("dn_number.normalized", dn_number)
    append_if_present("status", status)
    append_if_present("status_delivery", status_delivery)
    append_if_present("status_site", status_site)
    append_if_present("remark", remark)
    append_if_present("lng", lng)
    append_if_present("lat", lat)
    append_if_present("updated_by", updated_by)
    append_if_present("phone_number", phone_number)

    if photo_metadata:
        for key, value in photo_metadata.items():
            append_if_present(f"photo.{key}", value)

    if payload_entries:
        formatted_payload = "; ".join(f"{key} = {value!r}" for key, value in payload_entries) + ";"
        logger.info("Received DN update payload: %s", formatted_payload)

    photo_url = None
    if photo and photo.filename:
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    lng_val = str(lng) if lng else None
    lat_val = str(lat) if lat else None

    updated_by_value = None
    if updated_by is not None:
        updated_by_value = updated_by.strip() or None

    phone_number_value = None
    if isinstance(phone_number, str):
        phone_number_value = phone_number.strip() or None

    # legacy 兼容：如果 status_delivery 为空但有 status，则用 status 作为 status_delivery
    if (not status_delivery or status_delivery.strip() == "") and status:
        status_delivery = status

    existing_dn = db.query(DN).filter(DN.dn_number == dn_number).filter(_ACTIVE_DN_EXPR).one_or_none()
    gs_sheet_name = existing_dn.gs_sheet if existing_dn is not None else None
    raw_gs_row = existing_dn.gs_row if existing_dn is not None else None

    if isinstance(raw_gs_row, int):
        gs_row_index: int | None = raw_gs_row
    elif isinstance(raw_gs_row, str):
        try:
            gs_row_index = int(raw_gs_row)
        except ValueError:
            gs_row_index = None
    else:
        gs_row_index = None

    ensure_payload: dict[str, Any] = {
        "remark": remark,
        "photo_url": photo_url,
        "lng": lng_val,
        "lat": lat_val,
    }
    if status_site:
        ensure_payload["status_site"] = status_site
    if status_delivery:
        ensure_payload["status_delivery"] = status_delivery
    if updated_by_value is not None:
        ensure_payload["last_updated_by"] = updated_by_value
    if phone_number_value is not None:
        ensure_payload["driver_contact_number"] = phone_number_value

    status_upper = (status_delivery or "").strip().upper()
    timestamp_value: str | None = None
    if status_upper in ARRIVAL_STATUSES or status_upper in DEPARTURE_STATUSES:
        timestamp_value = _current_timestamp_gmt7()
    if status_upper in ARRIVAL_STATUSES and timestamp_value is not None:
        ensure_payload["actual_arrive_time_ata"] = timestamp_value
    if status_upper in DEPARTURE_STATUSES and timestamp_value is not None:
        ensure_payload["actual_depart_from_start_point_atd"] = timestamp_value

    # Ensure DN exists / update fields from payload; capture returned DN row
    dn_row = ensure_dn(db, dn_number, **ensure_payload)

    rec = add_dn_record(
        db,
        dn_number=dn_number,
        status_delivery=status_delivery,
        status_site=status_site,
        remark=remark,
        photo_url=photo_url,
        lng=lng_val,
        lat=lat_val,
        updated_by=updated_by_value,
        phone_number=phone_number_value,
    )
    logger.info(f"Added DN record: {dn_number}")

    checkin_payload = {
        "dn_id": dn_number,
        "status": (status_delivery or status or "").strip(),
        "driver_name": updated_by_value or "",
        "driver_phone": phone_number_value or "",
        "check_in_time": datetime.now(TZ_GMT7).strftime("%Y-%m-%d %H:%M:%S"),
        "longitude": lng_val or "",
        "latitude": lat_val or "",
    }
    if photo_url:
        checkin_payload["photo_url"] = photo_url

    background_tasks.add_task(
        _run_post_update_tasks,
        dn_number=dn_number,
        status_delivery=status_delivery,
        status_site=status_site,
        remark=remark,
        updated_by_value=updated_by_value,
        phone_number_value=phone_number_value,
        gs_sheet_name=gs_sheet_name,
        gs_row_index=gs_row_index,
        dn_row_id=getattr(dn_row, "id", None),
        checkin_payload=checkin_payload,
    )

    return {"ok": True, "id": rec.id, "photo": photo_url}


@router.post("/check_result")
@router.post("/dn_checker")
def upload_check_result(
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    report_id = payload.get("reportId") or payload.get("report_id") or payload.get("id")
    dn_number = payload.get("dnNumber") or payload.get("dn_number") or payload.get("dn")
    if not report_id or not dn_number:
        raise HTTPException(status_code=400, detail="report_id and dn_number are required")

    normalized_dn = normalize_dn(str(dn_number))
    if not DN_RE.fullmatch(normalized_dn):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    boxes = payload.get("boxes")
    metadata = payload.get("metadata")
    if boxes is not None and not isinstance(boxes, list):
        raise HTTPException(status_code=400, detail="boxes must be a list")
    if metadata is not None and not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be an object")

    existing = (
        db.query(CheckResult)
        .filter(CheckResult.report_id == str(report_id))
        .one_or_none()
    )

    if existing is None:
        record = CheckResult(
            report_id=str(report_id),
            dn_number=normalized_dn,
            lsp=payload.get("lsp"),
            checker_name=payload.get("checkerName") or payload.get("checker_name"),
            check_time=payload.get("checkTime") or payload.get("check_time"),
            status=payload.get("status"),
            box_count=_coerce_int(payload.get("boxCount")),
            checked_count=_coerce_int(payload.get("checkedCount")),
            boxes_json=json.dumps(boxes) if boxes is not None else None,
            metadata_json=json.dumps(metadata) if metadata is not None else None,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
    else:
        existing.dn_number = normalized_dn
        existing.lsp = payload.get("lsp")
        existing.checker_name = payload.get("checkerName") or payload.get("checker_name")
        existing.check_time = payload.get("checkTime") or payload.get("check_time")
        existing.status = payload.get("status")
        existing.box_count = _coerce_int(payload.get("boxCount"))
        existing.checked_count = _coerce_int(payload.get("checkedCount"))
        existing.boxes_json = json.dumps(boxes) if boxes is not None else None
        existing.metadata_json = json.dumps(metadata) if metadata is not None else None
        db.add(existing)
        db.commit()
        db.refresh(existing)
        record = existing

    return {
        "ok": True,
        "id": record.id,
        "report_id": record.report_id,
        "dn_number": record.dn_number,
        "lsp": record.lsp,
        "checker_name": record.checker_name,
        "status": record.status,
        "box_count": record.box_count,
        "checked_count": record.checked_count,
    }


@router.get("/check_result")
def list_check_results(
    date_value: date | None = Query(None, alias="date", description="Record created date in YYYY-MM-DD; defaults to today in GMT+7"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    dn_number: str | None = Query(None, description="Optional DN number filter"),
    db: Session = Depends(get_db),
):
    start, end = _check_result_created_date_range(date_value)
    query = db.query(CheckResult).filter(
        CheckResult.created_at >= start,
        CheckResult.created_at <= end,
    )

    if dn_number and dn_number.strip():
        normalized_dn = normalize_dn(dn_number)
        if normalized_dn:
            query = query.filter(CheckResult.dn_number.ilike(f"%{normalized_dn}%"))

    total = query.count()
    records = (
        query.order_by(CheckResult.created_at.desc(), CheckResult.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "ok": True,
        "date": (date_value or datetime.now(TZ_GMT7).date()).isoformat(),
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_serialize_check_result(record) for record in records],
    }


@router.get("/check_result/{result_id}")
def get_check_result(
    result_id: int,
    db: Session = Depends(get_db),
):
    record = db.query(CheckResult).filter(CheckResult.id == result_id).one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Check result not found")
    return {"ok": True, "item": _serialize_check_result(record, include_details=True)}


@router.post("/batch_update")
def batch_update_dn(
    dn_numbers: List[str] = Body(..., description="JSON array of DN numbers to create"),
    db: Session = Depends(get_db),
):
    if not dn_numbers:
        return {
            "status": "fail",
            "errmessage": "DN number 列表为空",
            "success_count": 0,
            "failure_count": 0,
            "success_dn_numbers": [],
            "failure_details": {},
        }

    normalized_numbers: List[str] = []
    failure_details: dict[str, str] = {}
    seen_numbers: set[str] = set()

    def add_failure(number: str, reason: str) -> None:
        failure_details[number] = reason

    for raw_number in dn_numbers:
        normalized = normalize_dn(raw_number)
        if not normalized or not DN_RE.fullmatch(normalized):
            base_key = raw_number if isinstance(raw_number, str) and raw_number else "<empty>"
            failure_key = str(base_key) if base_key is not None else "<empty>"
            add_failure(failure_key, "无效的 DN number")
            continue
        if normalized in seen_numbers:
            add_failure(normalized, "请求中重复")
            continue
        seen_numbers.add(normalized)
        normalized_numbers.append(normalized)

    existing_numbers = get_existing_dn_numbers(db, normalized_numbers)
    success_numbers: List[str] = []

    for number in normalized_numbers:
        if number in existing_numbers:
            add_failure(number, "DN number 已存在")
            continue
        ensure_dn(db, number, status_delivery="NO STATUS", status_site=None)
        add_dn_record(db, dn_number=number, status_delivery="NO STATUS", status_site=None, remark=None, photo_url=None, lng=None, lat=None)
        success_numbers.append(number)

    status_value = "ok" if success_numbers else "fail"
    return {
        "status": status_value,
        "success_count": len(success_numbers),
        "failure_count": len(failure_details),
        "success_dn_numbers": success_numbers,
        "failure_details": failure_details,
    }


@router.delete("/update/{id}")
def remove_dn_record(id: int, db: Session = Depends(get_db)):
    deleted_record = delete_dn_record(db, id)
    if deleted_record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    logger.info("Deleted DN record: %s", _format_log_entries(deleted_record))
    return {"ok": True}


@router.delete("/{dn_number}")
def remove_dn(dn_number: str, db: Session = Depends(get_db)):
    normalized_number = normalize_dn(dn_number)
    if not DN_RE.fullmatch(normalized_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    deleted = delete_dn(db, normalized_number)
    if deleted is None:
        raise HTTPException(status_code=404, detail="DN not found")
    logger.info("Deleted DN: %s", _format_log_entries(deleted["dn"]))
    if deleted["records"]:
        for record in deleted["records"]:
            logger.info("Deleted DN related record: %s", _format_log_entries(record))
    return {"ok": True}
