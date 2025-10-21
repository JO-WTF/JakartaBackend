"""DN listing endpoints."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.crud import get_latest_dn_records_map, list_all_dn_records, list_dn_by_dn_numbers, list_dn_by_du_ids, search_dn_list
from app.db import get_db
from app.dn_columns import get_sheet_columns
from app.models import DN, DNRecord
from app.utils.query import normalize_batch_dn_numbers
from app.utils.time import TZ_GMT7, parse_gmt7_date_range, parse_plan_mos_date, to_gmt7_iso
from app.core.sync import _normalize_status_delivery_value
from app.core.google import make_gs_cell_url
from app.api.dn.stats import _normalize_lsp_label
from app.constants import EARLY_BIRD_AREA_THRESHOLDS

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


def _normalize_text_label(value: Optional[str]) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(value.strip().split())
    return collapsed.lower() if collapsed else None


def _normalize_area_label(value: Optional[str]) -> str | None:
    return _normalize_text_label(value)


def _get_area_threshold(area: Optional[str]) -> int | None:
    normalized = _normalize_area_label(area)
    if not normalized:
        return None
    return EARLY_BIRD_AREA_THRESHOLDS.get(normalized)


def _to_jakarta(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_GMT7)


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
    mos_type: Optional[List[str]] = Query(None, description="MOS type"),
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
    mos_type_values = _collect_query_values(mos_type)
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
        mos_type_values=mos_type_values,
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


@router.get("/list/early-bird")
def list_early_bird_dn(
    start_date: date = Query(..., description="起始 Plan MOS 日期 (YYYY-MM-DD)"),
    end_date: date = Query(..., description="结束 Plan MOS 日期 (YYYY-MM-DD)"),
    region: Optional[List[str]] = Query(None, description="按 Region 过滤 (不区分大小写)"),
    area: Optional[List[str]] = Query(None, description="按 Area 过滤 (不区分大小写)"),
    lsp: Optional[List[str]] = Query(None, description="按 LSP 过滤 (不区分大小写)"),
    db: Session = Depends(get_db),
):
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date must be on or after start_date")

    def _make_filter(values: list[str] | None, normalizer) -> set[str] | None:
        if not values:
            return None
        normalized_values: list[str] = []
        for value in values:
            normalized = normalizer(value)
            if normalized:
                normalized_values.append(normalized)
        return set(normalized_values)

    region_values = _collect_query_values(region)
    area_values = _collect_query_values(area)
    lsp_values = _collect_query_values(lsp)

    region_filter_set = _make_filter(region_values, _normalize_text_label)
    area_filter_set = _make_filter(area_values, _normalize_area_label)
    lsp_filter_set = _make_filter(lsp_values, _normalize_text_label)

    base_query = (
        db.query(DN)
        .filter(DN.plan_mos_date.isnot(None))
        .filter(func.length(func.trim(DN.plan_mos_date)) > 0)
    )

    candidates: Dict[str, dict[str, Any]] = {}
    for dn in base_query:
        plan_date = parse_plan_mos_date(getattr(dn, "plan_mos_date", None))
        if plan_date is None or plan_date < start_date or plan_date > end_date:
            continue

        region_value = _normalize_text_label(getattr(dn, "region", None))
        if region_filter_set is not None and region_value not in region_filter_set:
            continue

        area_value_raw = getattr(dn, "area", None)
        area_value = _normalize_area_label(area_value_raw)
        if area_filter_set is not None and area_value not in area_filter_set:
            continue

        lsp_value = _normalize_text_label(getattr(dn, "lsp", None))
        if lsp_filter_set is not None and lsp_value not in lsp_filter_set:
            continue

        threshold_hour = _get_area_threshold(area_value_raw)
        if threshold_hour is None:
            continue

        candidates[dn.dn_number] = {
            "dn": dn,
            "plan_date": plan_date,
            "threshold_hour": threshold_hour,
        }

    if not candidates:
        return {
            "ok": True,
            "total": 0,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "data": [],
        }

    dn_numbers = list(candidates.keys())
    normalized_status = func.upper(func.trim(DNRecord.status_delivery))
    arrival_records = (
        db.query(DNRecord)
        .filter(DNRecord.dn_number.in_(dn_numbers))
        .filter(DNRecord.status_delivery.isnot(None))
        .filter(normalized_status.in_(("ARRIVED AT SITE", "POD")))
        .order_by(DNRecord.dn_number.asc(), DNRecord.created_at.asc(), DNRecord.id.asc())
        .all()
    )

    latest_arrivals: dict[str, dict[str, Any]] = {}
    for record in arrival_records:
        meta = candidates.get(record.dn_number)
        if not meta:
            continue

        arrival_time = _to_jakarta(record.created_at)
        if arrival_time is None:
            continue
        if arrival_time.date() != meta["plan_date"]:
            continue

        raw_status = (record.status_delivery or "").strip().upper()
        if raw_status not in {"ARRIVED AT SITE", "POD"}:
            continue

        updater = (record.updated_by or "").strip().lower()
        if updater != "driver":
            continue

        priority = 0 if raw_status == "ARRIVED AT SITE" else 1
        existing = latest_arrivals.get(record.dn_number)
        if (
            existing is None
            or priority < existing["priority"]
            or (priority == existing["priority"] and arrival_time > existing["arrival_time"])
        ):
            latest_arrivals[record.dn_number] = {
                "arrival_time": arrival_time,
                "priority": priority,
                "status": raw_status,
                "record": record,
            }

    if not latest_arrivals:
        return {
            "ok": True,
            "total": 0,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "data": [],
        }

    raw_results: list[tuple[DN, date, datetime, datetime, DNRecord, str]] = []
    for dn_number, meta in candidates.items():
        arrival_meta = latest_arrivals.get(dn_number)
        if not arrival_meta:
            continue

        arrival_time = arrival_meta["arrival_time"]
        record = arrival_meta["record"]
        status_label = arrival_meta["status"]
        cutoff_time = datetime.combine(
            meta["plan_date"],
            time(meta["threshold_hour"], 0, tzinfo=TZ_GMT7),
        )
        if arrival_time >= cutoff_time:
            continue

        raw_results.append((meta["dn"], meta["plan_date"], arrival_time, cutoff_time, record, status_label))

    raw_results.sort(key=lambda item: (item[1], item[2], item[0].dn_number))

    data = [
        {
            "dn_id": dn.id,
            "dn_number": dn.dn_number,
            "area": dn.area,
            "region": dn.region,
            "lsp": dn.lsp,
            "plan_mos_date": dn.plan_mos_date,
            "plan_mos_date_iso": plan_date.isoformat(),
            "arrival_record_id": record.id,
            "arrived_at_site_time": to_gmt7_iso(arrival_time),
            "cutoff_time": to_gmt7_iso(cutoff_time),
            "is_deleted": dn.is_deleted,
            "arrival_status": status_label,
            "record_created_at": to_gmt7_iso(record.created_at),
            "record_updated_by": record.updated_by,
            "record_phone_number": record.phone_number,
            "record_lat": record.lat,
            "record_lng": record.lng,
            "record_photo_url": record.photo_url,
        }
        for dn, plan_date, arrival_time, cutoff_time, record, status_label in raw_results
    ]

    return {
        "ok": True,
        "total": len(data),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "data": data,
    }


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
