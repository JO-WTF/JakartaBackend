"""DN export endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.crud import get_dn_map_by_numbers
from app.db import get_db
from app.dn_columns import ensure_dynamic_columns_loaded
from app.models import DN, DNRecord
from app.services.dn_pdf import generate_dn_details_pdf
from app.settings import settings
from app.utils.query import normalize_batch_dn_numbers
from app.utils.time import to_gmt7_iso

router = APIRouter(prefix="/api/dn")


def _serialize_datetime(value: Any) -> Any:
    if isinstance(value, datetime):
        return to_gmt7_iso(value)
    return value


def _serialize_dn_row(dn: DN | None) -> Dict[str, Any] | None:
    if dn is None:
        return None
    row: Dict[str, Any] = {}
    for column in DN.__table__.columns:
        column_name = column.name
        row[column_name] = _serialize_datetime(getattr(dn, column_name, None))
    return row


def _serialize_record(record: DNRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "dn_number": record.dn_number,
        "status_delivery": record.status_delivery,
        "status_site": record.status_site,
        "remark": record.remark,
        "photo_url": record.photo_url,
        "lng": record.lng,
        "lat": record.lat,
        "updated_by": record.updated_by,
        "phone_number": record.phone_number,
        "created_at": to_gmt7_iso(record.created_at),
    }


def _collect_dn_export_entries(db: Session, numbers: List[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    ensure_dynamic_columns_loaded(db)
    dn_map = get_dn_map_by_numbers(db, numbers)

    records = (
        db.query(DNRecord)
        .filter(DNRecord.dn_number.in_(numbers))
        .order_by(DNRecord.dn_number.asc(), DNRecord.created_at.asc(), DNRecord.id.asc())
        .all()
    )

    records_by_dn: Dict[str, List[Dict[str, Any]]] = {number: [] for number in numbers}
    for record in records:
        records_by_dn.setdefault(record.dn_number, []).append(_serialize_record(record))

    data: List[Dict[str, Any]] = []
    not_found: List[str] = []

    for number in numbers:
        dn_row = dn_map.get(number)
        if dn_row is None:
            not_found.append(number)
        data.append(
            {
                "dn_number": number,
                "dn": _serialize_dn_row(dn_row),
                "records": records_by_dn.get(number, []),
            }
        )

    return data, not_found


@router.get("/export/details")
def export_dn_details(
    dn_number: List[str] | None = Query(None, description="DN number (支持多个)"),
    db: Session = Depends(get_db),
):
    numbers = normalize_batch_dn_numbers(dn_number)
    data, not_found = _collect_dn_export_entries(db, numbers)

    response: Dict[str, Any] = {
        "ok": True,
        "count": len(data),
        "data": data,
    }
    if not_found:
        response["not_found_dn_numbers"] = not_found
    return response


@router.get("/export/details-pdf")
def export_dn_details_pdf(
    dn_number: List[str] | None = Query(None, description="DN number (支持多个)"),
    db: Session = Depends(get_db),
):
    numbers = normalize_batch_dn_numbers(dn_number)
    data, not_found = _collect_dn_export_entries(db, numbers)

    if not data:
        raise HTTPException(status_code=404, detail="No DN data available for the provided numbers")

    mapbox_token = settings.mapbox_access_token
    if not mapbox_token:
        raise HTTPException(status_code=500, detail="MAPBOX_ACCESS_TOKEN is not configured")

    pdf_bytes = generate_dn_details_pdf(
        data,
        mapbox_token=mapbox_token,
        storage_base_path=settings.storage_disk_path,
    )

    filename = f"dn-details-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.pdf"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if not_found:
        headers["X-Not-Found-DN"] = ",".join(not_found)

    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
