"""DN listing endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import DN_RE
from app.crud import get_latest_dn_records_map, list_all_dn_records, list_dn_by_dn_numbers, search_dn_list
from app.db import get_db
from app.dn_columns import get_sheet_columns
from app.models import DN
from app.utils.query import normalize_batch_dn_numbers
from app.utils.string import normalize_dn
from app.utils.time import parse_gmt7_date_range, to_gmt7_iso

router = APIRouter(prefix="/api/dn")


def _collect_query_values(*values: Any) -> list[str] | None:
    """Collect query parameter values supporting repeated parameters and comma-separated values.

    Matches the legacy main branch implementation to preserve behaviour.
    """

    normalized: list[str] = []
    seen: set[str] = set()

    def _add_candidate(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return
        parts = candidate.split(",") if "," in candidate else [candidate]
        for part in parts:
            trimmed = part.strip()
            if trimmed and trimmed not in seen:
                seen.add(trimmed)
                normalized.append(trimmed)

    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            _add_candidate(value)
            continue
        try:
            iterator: Iterable[Any] = iter(value)  # type: ignore[arg-type]
        except TypeError:
            continue
        for candidate in iterator:
            _add_candidate(candidate)

    return normalized or None


@router.get("/list")
async def get_dn_list(db: Session = Depends(get_db)):
    items = (
        db.query(DN)
        .filter(func.coalesce(DN.is_deleted, "N") == "N")
        .order_by(DN.dn_number.asc())
        .all()
    )
    if not items:
        return {"ok": True, "data": []}

    latest_records = get_latest_dn_records_map(db, [it.dn_number for it in items])
    sheet_columns = get_sheet_columns()

    data: List[dict[str, Any]] = []
    for it in items:
        row: dict[str, Any] = {
            "id": it.id,
            "dn_number": it.dn_number,
            "created_at": to_gmt7_iso(it.created_at),
            "status": it.status,
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "last_updated_by": it.last_updated_by,
            "gs_sheet": it.gs_sheet,
            "gs_row": it.gs_row,
        }
        for column in sheet_columns:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = to_gmt7_iso(latest.created_at if latest else None)
        data.append(row)

    return {"ok": True, "data": data}


@router.get("/list/search")
def search_dn_list_api(
    date: Optional[List[str]] = Query(None, description="Plan MOS date"),
    dn_number: str | None = Query(None, description="DN number"),
    dnnumber_legacy: str | None = Query(None, alias="dnnumber", description="DN number (legacy alias)", include_in_schema=False),
    du_id: str | None = Query(None, description="关联 DU ID"),
    status_delivery: Optional[List[str]] = Query(None, description="Status delivery"),
    status_delivery_legacy: Optional[List[str]] = Query(
        None, alias="statusDelivery", description="Status delivery (legacy alias)", include_in_schema=False
    ),
    status_values_param: Optional[List[str]] = Query(None, alias="status", description="Status"),
    status_not_empty: bool | None = Query(None, description="仅返回状态不为空的 DN 记录"),
    has_coordinate: bool | None = Query(None, description="根据是否存在经纬度筛选 DN 记录"),
    show_deleted: bool = Query(False, description="是否显示已软删除的记录"),
    lsp: Optional[List[str]] = Query(None, description="LSP"),
    region: Optional[List[str]] = Query(None, description="Region"),
    area: str | None = Query(None, description="Area"),
    status_wh: Optional[List[str]] = Query(None, description="Status WH"),
    subcon: Optional[List[str]] = Query(None, description="Subcon"),
    project: str | None = Query(None, description="Project request"),
    date_from: datetime | None = Query(None, description="Last modified start time (ISO 8601)"),
    date_to: datetime | None = Query(None, description="Last modified end time (ISO 8601)"),
    page: int = Query(1, ge=1),
    page_size: str | int = Query(20, description="Page size (number or 'all' for all records)"),
    db: Session = Depends(get_db),
):
    # Handle page_size parameter
    if isinstance(page_size, str) and page_size.lower() == "all":
        actual_page_size = None  # None means no limit
        page = 1  # Force page to 1 when getting all records
    else:
        try:
            actual_page_size = int(page_size)
            if actual_page_size < 1:
                raise HTTPException(status_code=400, detail="Page size must be positive")
            if actual_page_size > 2000:
                raise HTTPException(status_code=400, detail="Page size cannot exceed 2000 (use 'all' for unlimited)")
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Page size must be a number or 'all'")

    dn_query_value = dn_number or dnnumber_legacy
    dn_number = normalize_dn(dn_query_value) if dn_query_value else None
    if dn_number and not DN_RE.fullmatch(dn_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    plan_mos_dates = _collect_query_values(date)
    status_delivery_values = _collect_query_values(status_delivery, status_delivery_legacy)
    status_values = _collect_query_values(status_values_param)
    lsp_values = _collect_query_values(lsp)
    region_values = _collect_query_values(region)
    status_wh_values = _collect_query_values(status_wh)
    subcon_values = _collect_query_values(subcon)
    area_value = area.strip() if area else None
    project_value = project.strip() if project else None
    modified_from, modified_to = parse_gmt7_date_range(date_from, date_to)

    total, items = search_dn_list(
        db,
        plan_mos_dates=plan_mos_dates,
        dn_number=dn_number,
        du_id=du_id,
        status_delivery_values=status_delivery_values,
        status_values=status_values,
        status_not_empty=status_not_empty,
        has_coordinate=has_coordinate,
        show_deleted=show_deleted,
        lsp_values=lsp_values,
        region_values=region_values,
        area=area_value,
        status_wh_values=status_wh_values,
        subcon_values=subcon_values,
        project_request=project_value,
        last_modified_from=modified_from,
        last_modified_to=modified_to,
        page=page,
        page_size=actual_page_size,
    )

    if not items:
        return {"ok": True, "total": total, "page": page, "page_size": page_size, "items": []}

    latest_records = get_latest_dn_records_map(db, [it.dn_number for it in items])
    sheet_columns = get_sheet_columns()

    data: List[dict[str, Any]] = []
    for it in items:
        row: dict[str, Any] = {
            "id": it.id,
            "dn_number": it.dn_number,
            "created_at": to_gmt7_iso(it.created_at),
            "status": it.status,
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "last_updated_by": it.last_updated_by,
            "gs_sheet": it.gs_sheet,
            "gs_row": it.gs_row,
        }
        for column in sheet_columns:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = to_gmt7_iso(latest.created_at if latest else None)
        data.append(row)

    return {"ok": True, "total": total, "page": page, "page_size": page_size, "items": data}


@router.get("/records")
def get_all_dn_records(db: Session = Depends(get_db)):
    items = list_all_dn_records(db)
    return {
        "ok": True,
        "total": len(items),
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "status": it.status,
                "remark": it.remark,
                "photo_url": it.photo_url,
                "lng": it.lng,
                "lat": it.lat,
                "updated_by": it.updated_by,
                "created_at": to_gmt7_iso(it.created_at),
            }
            for it in items
        ],
    }


@router.get("/list/batch")
def batch_search_dn_list(
    dn_number: Optional[List[str]] = Query(None, description="重复 dn_number 或逗号分隔"),
    dnnumber_legacy: Optional[List[str]] = Query(
        None, alias="dnnumber", description="重复 dn_number 或逗号分隔 (legacy alias)", include_in_schema=False
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    dn_numbers = normalize_batch_dn_numbers(dn_number, dnnumber_legacy)

    total, items = list_dn_by_dn_numbers(db, dn_numbers, page=page, page_size=page_size)

    if not items:
        return {"ok": True, "total": total, "page": page, "page_size": page_size, "items": []}

    latest_records = get_latest_dn_records_map(db, [it.dn_number for it in items])
    sheet_columns = get_sheet_columns()

    data: list[dict[str, Any]] = []
    for it in items:
        row: dict[str, Any] = {
            "id": it.id,
            "dn_number": it.dn_number,
            "created_at": to_gmt7_iso(it.created_at),
            "status": it.status,
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "last_updated_by": it.last_updated_by,
            "gs_sheet": it.gs_sheet,
            "gs_row": it.gs_row,
        }
        for column in sheet_columns:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = to_gmt7_iso(latest.created_at if latest else None)
        data.append(row)

    return {"ok": True, "total": total, "page": page, "page_size": page_size, "items": data}
