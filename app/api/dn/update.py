"""DN update and mutation endpoints."""

from __future__ import annotations

from typing import Any, List, Optional
from datetime import datetime

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
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
from app.db import get_db
from app.models import DN
from app.storage import save_file
from app.utils.string import normalize_dn
from app.utils.time import TZ_GMT7
from app.core.sheet import sync_dn_status_to_sheet

router = APIRouter(prefix="/api/dn")


def _current_timestamp_gmt7() -> str:
    now = datetime.now(TZ_GMT7)
    return f"{now.month}/{now.day}/{now.year} {now.hour}:{now.minute:02d}:{now.second:02d}"


@router.post("/update")
def update_dn(
    dnNumber: str = Form(...),
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

    ensure_dn(db, dn_number, **ensure_payload)

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

    gspread_update_result: dict[str, Any] | None = None
    gspread_timestamp_result: dict[str, Any] | None = None
    should_check_sheet = (
        gs_sheet_name and isinstance(gs_row_index, int) and gs_row_index > 0 and status_delivery is not None
    )

    if should_check_sheet:
        gspread_update_result = sync_dn_status_to_sheet(
            gs_sheet_name, gs_row_index, dn_number, status_delivery, status_site, updated_by_value
        )

    response: dict[str, Any] = {"ok": True, "id": rec.id, "photo": photo_url}
    if gspread_update_result is not None:
        response["delivery_status_update_result"] = gspread_update_result
    if gspread_timestamp_result is not None:
        response["timestamp_update_result"] = gspread_timestamp_result
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
