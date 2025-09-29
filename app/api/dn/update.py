"""DN update and mutation endpoints."""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.sheet import sync_delivery_status_to_sheet
from app.core.sync import DN_RE, VALID_STATUSES
from app.crud import (
    add_dn_record,
    delete_dn,
    delete_dn_record,
    ensure_dn,
    get_existing_dn_numbers,
    update_dn_record,
)
from app.db import get_db
from app.models import DN
from app.storage import save_file
from app.utils.string import normalize_dn

router = APIRouter(prefix="/api/dn")


@router.post("/update")
def update_dn(
    dnNumber: str = Form(...),
    status: str = Form(...),
    delivery_status: str | None = Form(None),
    status_delivery: str | None = Form(None, description="Backward compatibility alias for delivery_status form field"),
    remark: str | None = Form(None),
    photo: UploadFile | None = File(None),
    lng: str | float | None = Form(None),
    lat: str | float | None = Form(None),
    updated_by: str | None = Form(None),
    db: Session = Depends(get_db),
):
    dn_number = normalize_dn(dnNumber)
    if not DN_RE.fullmatch(dn_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    photo_url = None
    if photo and photo.filename:
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    lng_val = str(lng) if lng else None
    lat_val = str(lat) if lat else None

    updated_by_value = None
    if updated_by is not None:
        updated_by_value = updated_by.strip() or None

    delivery_status_raw = delivery_status if delivery_status is not None else status_delivery
    delivery_status_value = (delivery_status_raw or "").strip() or None

    if delivery_status_value is None:
        delivery_status_value = "On Site" if status == "ARRIVED AT SITE" else "On The Way"

    existing_dn = db.query(DN).filter(DN.dn_number == dn_number).one_or_none()
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
        "status": status,
        "remark": remark,
        "photo_url": photo_url,
        "lng": lng_val,
        "lat": lat_val,
    }
    if delivery_status_value is not None:
        ensure_payload["status_delivery"] = delivery_status_value
    if updated_by_value is not None:
        ensure_payload["last_updated_by"] = updated_by_value

    ensure_dn(db, dn_number, **ensure_payload)

    rec = add_dn_record(
        db,
        dn_number=dn_number,
        status=status,
        remark=remark,
        photo_url=photo_url,
        lng=lng_val,
        lat=lat_val,
        updated_by=updated_by_value,
    )

    gspread_update_result: dict[str, Any] | None = None
    should_check_sheet = (
        gs_sheet_name and isinstance(gs_row_index, int) and gs_row_index > 0 and delivery_status_value is not None
    )

    if should_check_sheet:
        gspread_update_result = sync_delivery_status_to_sheet(
            gs_sheet_name, gs_row_index, dn_number, delivery_status_value
        )

    response: dict[str, Any] = {"ok": True, "id": rec.id, "photo": photo_url}
    if gspread_update_result is not None:
        response["delivery_status_update_result"] = gspread_update_result
    return response


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
        ensure_dn(db, number, status="NO STATUS")
        add_dn_record(db, dn_number=number, status="NO STATUS", remark=None, photo_url=None, lng=None, lat=None)
        success_numbers.append(number)

    status_value = "ok" if success_numbers else "fail"
    return {
        "status": status_value,
        "success_count": len(success_numbers),
        "failure_count": len(failure_details),
        "success_dn_numbers": success_numbers,
        "failure_details": failure_details,
    }


@router.put("/update/{id}")
def edit_dn_record(
    id: int,
    status: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    updated_by: Optional[str] = Form(None),
    photo: UploadFile | None = File(None),
    json_body: Optional[dict] = Body(None),
    db: Session = Depends(get_db),
):
    updated_by_provided = updated_by is not None

    if json_body and isinstance(json_body, dict):
        if "status" in json_body:
            status = json_body.get("status")
        if "remark" in json_body:
            remark = json_body.get("remark")
        if "updated_by" in json_body:
            updated_by = json_body.get("updated_by")
            updated_by_provided = True

    if status is not None and status.strip() == "":
        status = None
    if remark is not None:
        remark = remark.strip()
        if remark == "":
            remark = None
        elif len(remark) > 1000:
            raise HTTPException(status_code=400, detail="remark too long (max 1000 chars)")

    if isinstance(updated_by, str):
        updated_by = updated_by.strip() or None
    elif updated_by_provided and updated_by is not None:
        updated_by = str(updated_by)

    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    photo_url = None
    if photo and getattr(photo, "filename", None):
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    rec = update_dn_record(
        db,
        rec_id=id,
        status=status,
        remark=remark,
        photo_url=photo_url,
        updated_by=updated_by,
        updated_by_set=updated_by_provided,
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")

    ensure_payload: dict[str, Any] = {
        "status": rec.status,
        "remark": rec.remark,
        "photo_url": rec.photo_url,
        "lng": rec.lng,
        "lat": rec.lat,
    }
    if updated_by_provided:
        ensure_payload["last_updated_by"] = rec.updated_by

    ensure_dn(db, rec.dn_number, **ensure_payload)
    return {"ok": True, "id": rec.id}


@router.delete("/update/{id}")
def remove_dn_record(id: int, db: Session = Depends(get_db)):
    ok = delete_dn_record(db, id)
    if not ok:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"ok": True}


@router.delete("/{dn_number}")
def remove_dn(dn_number: str, db: Session = Depends(get_db)):
    normalized_number = normalize_dn(dn_number)
    if not DN_RE.fullmatch(normalized_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    ok = delete_dn(db, normalized_number)
    if not ok:
        raise HTTPException(status_code=404, detail="DN not found")
    return {"ok": True}
