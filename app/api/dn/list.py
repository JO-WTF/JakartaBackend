"""DN listing endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.crud import get_latest_dn_records_map, list_all_dn_records, list_dn_by_dn_numbers, list_dn_by_du_ids, search_dn_list
from app.db import get_db
from app.dn_columns import get_sheet_columns
from app.models import DN
from app.utils.query import normalize_batch_dn_numbers
from app.utils.time import parse_gmt7_date_range, to_gmt7_iso, TZ_GMT7
from app.core.sync import _normalize_status_delivery_value
from app.core.google import make_gs_cell_url
from app.api.dn.stats import _normalize_lsp_label

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


def _normalize_batch_du_ids(values: Optional[List[str]] | None) -> list[str]:
    du_ids = _collect_query_values(values)
    if not du_ids:
        raise HTTPException(status_code=400, detail="Missing du_id")
    return du_ids


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
            "status_delivery": getattr(it, "status_delivery", None),
            "status_site": getattr(it, "status_site", None),
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "last_updated_by": it.last_updated_by,
            "gs_sheet": it.gs_sheet,
            "gs_row": it.gs_row,
            "gs_cell_url": make_gs_cell_url(getattr(it, "gs_sheet", None), getattr(it, "gs_row", None)),
            "is_deleted": it.is_deleted,
            "update_count": it.update_count,
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
    dn_number: Optional[List[str]] = Query(None, description="DN number (支持多个)"),
    du_id: str | None = Query(None, description="关联 DU ID"),
    phone_number: str | None = Query(None, description="Driver phone number"),
    status_delivery: Optional[List[str]] = Query(None, description="Status delivery"),
    status_site: Optional[List[str]] = Query(None, description="Status site"),
    status_delivery_not_empty: bool | None = Query(None, description="仅返回交付状态不为空的 DN 记录"),
    status_site_not_empty: bool | None = Query(None, description="仅返回现场状态不为空的 DN 记录"),
    has_coordinate: bool | None = Query(None, description="根据是否存在经纬度筛选 DN 记录"),
    show_deleted: bool = Query(False, description="是否显示已软删除的记录"),
    lsp: Optional[List[str]] = Query(None, description="LSP"),
    region: Optional[List[str]] = Query(None, description="Region"),
    area: Optional[List[str]] = Query(None, description="Area"),
    status_wh: Optional[List[str]] = Query(None, description="Status WH"),
    subcon: Optional[List[str]] = Query(None, description="Subcon"),
    project_request: Optional[List[str]] = Query(None, description="Project request (支持多个)"),
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

    # Process DN numbers - support multiple values
    dn_numbers: list[str] | None = None
    if dn_number:
        try:
            dn_numbers = normalize_batch_dn_numbers(dn_number)
        except HTTPException:
            # If no valid DN numbers, set to None instead of raising error
            dn_numbers = None

    plan_mos_dates = _collect_query_values(date)
    status_delivery_values = _collect_query_values(status_delivery)
    status_site_values = _collect_query_values(status_site)
    lsp_values = _collect_query_values(lsp)
    region_values = _collect_query_values(region)
    status_wh_values = _collect_query_values(status_wh)
    subcon_values = _collect_query_values(subcon)
    area_values = _collect_query_values(area)
    project_values = _collect_query_values(project_request)
    phone_number_value = phone_number.strip() if isinstance(phone_number, str) and phone_number.strip() else None
    modified_from, modified_to = parse_gmt7_date_range(date_from, date_to)

    # Fetch all matched records (no pagination) once, compute stats on full set,
    # then slice to return the requested page.
    total_all, all_items = search_dn_list(
        db,
        plan_mos_dates=plan_mos_dates,
        dn_numbers=dn_numbers,
        du_id=du_id,
        phone_number=phone_number_value,
        status_delivery_values=status_delivery_values,
        status_site_values=status_site_values,
        status_delivery_not_empty=status_delivery_not_empty,
        status_site_not_empty=status_site_not_empty,
        has_coordinate=has_coordinate,
        show_deleted=show_deleted,
        lsp_values=lsp_values,
        region_values=region_values,
        area=area_values,
        status_wh_values=status_wh_values,
        subcon_values=subcon_values,
        project_request=project_values,
        last_modified_from=modified_from,
        last_modified_to=modified_to,
        page=1,
        page_size=None,
    )

    # Now produce paginated slice from all_items
    if actual_page_size is None:
        # page_size 'all' -> return everything
        items = all_items
        total = total_all
    else:
        start = (page - 1) * actual_page_size
        end = start + actual_page_size
        items = all_items[start:end]
        total = total_all

    # Reuse central normalization helpers if available

    status_delivery_counts: dict[str, int] = {"Total": 0}
    status_site_counts: dict[str, int] = {}
    lsp_map: dict[str, dict[str, int]] = {}

    # If caller did not specify plan_mos_dates, stats should only count
    # records whose plan_mos_date equals today's date in GMT+7.
    if not plan_mos_dates:
        today_str = datetime.now(TZ_GMT7).strftime("%d %b %y")
    else:
        today_str = None

    for dn in all_items:
        # If no plan_mos_dates provided, only include records for today (GMT+7)
        if today_str is not None:
            dn_plan = getattr(dn, "plan_mos_date", None)
            if dn_plan is None or dn_plan.strip() != today_str:
                continue
        raw_sd = getattr(dn, "status_delivery", None)
        sd_norm = _normalize_status_delivery_value(raw_sd)
        sd = sd_norm if sd_norm is not None else "No Status"
        status_delivery_counts[sd] = status_delivery_counts.get(sd, 0) + 1
        status_delivery_counts["Total"] += 1

        ss_raw = getattr(dn, "status_site", None)
        if ss_raw is not None and isinstance(ss_raw, str):
            ss = ss_raw.strip()
            if ss:
                status_site_counts[ss] = status_site_counts.get(ss, 0) + 1

        lsp_label = _normalize_lsp_label(getattr(dn, "lsp", None), getattr(dn, "plan_mos_date", None))
        entry = lsp_map.setdefault(lsp_label, {"total_dn": 0, "status_not_empty": 0})
        entry["total_dn"] += 1
        # status_not_empty means status_delivery not empty/null
        sd_present = getattr(dn, "status_delivery", None)
        if sd_present is not None and str(sd_present).strip() and str(sd_present).lower() != "no status":
            entry["status_not_empty"] += 1

    lsp_summary = [
        {"lsp": lsp_value, "total_dn": vals["total_dn"], "status_not_empty": vals["status_not_empty"]}
        for lsp_value, vals in sorted(lsp_map.items())
    ]

    stats = {
        "status_delivery": status_delivery_counts,
        "status_site": status_site_counts,
        "lsp_summary": lsp_summary,
    }

    if not items:
        return {"ok": True, "total": total, "page": page, "page_size": page_size, "items": [], "stats": stats}

    latest_records = get_latest_dn_records_map(db, [it.dn_number for it in items])
    sheet_columns = get_sheet_columns()

    data: List[dict[str, Any]] = []
    for it in items:
        row: dict[str, Any] = {
            "id": it.id,
            "dn_number": it.dn_number,
            "created_at": to_gmt7_iso(it.created_at),
            "status_delivery": getattr(it, "status_delivery", None),
            "status_site": getattr(it, "status_site", None),
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "last_updated_by": it.last_updated_by,
            "gs_sheet": it.gs_sheet,
            "gs_row": it.gs_row,
            "gs_cell_url": make_gs_cell_url(getattr(it, "gs_sheet", None), getattr(it, "gs_row", None)),
            "is_deleted": it.is_deleted,
            "update_count": it.update_count,
        }
        for column in sheet_columns:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = to_gmt7_iso(latest.created_at if latest else None)
        data.append(row)

    return {"ok": True, "total": total, "page": page, "page_size": page_size, "items": data, "stats": stats}


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
                "status_delivery": getattr(it, "status_delivery", None),
                "status_site": getattr(it, "status_site", None),
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
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    dn_numbers = normalize_batch_dn_numbers(dn_number)

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
            "status_delivery": getattr(it, "status_delivery", None),
            "status_site": getattr(it, "status_site", None),
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "last_updated_by": it.last_updated_by,
            "gs_sheet": it.gs_sheet,
            "gs_row": it.gs_row,
            "gs_cell_url": make_gs_cell_url(getattr(it, "gs_sheet", None), getattr(it, "gs_row", None)),
            "is_deleted": it.is_deleted,
            "update_count": it.update_count,
        }
        for column in sheet_columns:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = to_gmt7_iso(latest.created_at if latest else None)
        data.append(row)

    return {"ok": True, "total": total, "page": page, "page_size": page_size, "items": data}


@router.get("/list/batch-by-du")
def batch_search_dn_list_by_du(
    du_id: Optional[List[str]] = Query(None, description="重复 du_id 或逗号分隔"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    du_ids = _normalize_batch_du_ids(du_id)

    total, items = list_dn_by_du_ids(db, du_ids, page=page, page_size=page_size)

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
            "status_delivery": getattr(it, "status_delivery", None),
            "status_site": getattr(it, "status_site", None),
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "last_updated_by": it.last_updated_by,
            "gs_sheet": it.gs_sheet,
            "gs_row": it.gs_row,
            "gs_cell_url": make_gs_cell_url(getattr(it, "gs_sheet", None), getattr(it, "gs_row", None)),
            "is_deleted": it.is_deleted,
            "update_count": it.update_count,
        }
        for column in sheet_columns:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = to_gmt7_iso(latest.created_at if latest else None)
        data.append(row)

    return {"ok": True, "total": total, "page": page, "page_size": page_size, "items": data}
