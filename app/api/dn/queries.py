"""Read-only DN routes for listing and searching data."""

from __future__ import annotations

from typing import Any, List, Optional

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.crud import (
    get_dn_status_delivery_counts,
    get_dn_unique_field_values,
    get_latest_dn_records_map,
    list_all_dn_records,
    list_dn_by_dn_numbers,
    list_dn_records,
    list_dn_records_by_dn_numbers,
    search_dn_list,
    search_dn_records,
)
from app.db import get_db
from app.dn_columns import get_sheet_columns
from app.time_utils import to_gmt7_iso

from ..common import (
    DN_RE,
    DU_RE,
    VALID_STATUS_DESCRIPTION,
    _normalize_batch_dn_numbers,
    normalize_dn,
    normalize_du,
)
from .router import router


@router.get("/search")
def search_dn_records_api(
    dn_number: Optional[str] = Query(None, description="精确 DN number"),
    du_id: Optional[str] = Query(None, description="关联 DU ID"),
    status: Optional[str] = Query(None, description=f"状态过滤，可选: {VALID_STATUS_DESCRIPTION}"),
    remark: Optional[str] = Query(None, description="备注关键词(模糊)"),
    has_photo: Optional[bool] = Query(None, description="是否必须带附件 true/false"),
    date_from: Optional[datetime] = Query(None, description="起始时间(ISO 8601)"),
    date_to: Optional[datetime] = Query(None, description="结束时间(ISO 8601)"),
    page: int = Query(1, ge=1),
    page_size: Optional[int] = Query(
        None, ge=1, description="每页数量，缺省时返回全部符合条件的数据"
    ),
    db: Session = Depends(get_db),
):
    if dn_number:
        dn_number = normalize_dn(dn_number)
        if not DN_RE.fullmatch(dn_number):
            raise HTTPException(status_code=400, detail=f"Invalid DN number: {dn_number}")
    if du_id:
        du_id = normalize_du(du_id)
        if not DU_RE.fullmatch(du_id):
            raise HTTPException(status_code=400, detail=f"Invalid DU ID: {du_id}")

    total, items = search_dn_records(
        db,
        dn_number=dn_number,
        du_id=du_id,
        status=status,
        remark_keyword=remark,
        has_photo=has_photo,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size if page_size is not None else len(items),
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
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


@router.get("/batch")
def batch_get_dn_records(
    dn_number: Optional[List[str]] = Query(None, description="重复 dn_number 或逗号分隔"),
    dnnumber_legacy: Optional[List[str]] = Query(
        None,
        alias="dnnumber",
        description="重复 dn_number 或逗号分隔 (legacy alias)",
        include_in_schema=False,
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        dn_numbers = _normalize_batch_dn_numbers(dn_number, dnnumber_legacy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    total, items = list_dn_records_by_dn_numbers(
        db,
        dn_numbers,
        page=page,
        page_size=page_size,
    )

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
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


@router.get("/filters")
def get_dn_filters(db: Session = Depends(get_db)):
    values = get_dn_unique_field_values(db)
    return {"ok": True, "data": values}


@router.get("/status-delivery/stats")
def get_dn_status_delivery_stats(
    lsp: Optional[str] = Query(None, description="LSP 过滤"),
    plan_mos_date: Optional[str] = Query(
        None, description="Plan MOS Date 过滤 (格式: 01 Sep 25)"
    ),
    db: Session = Depends(get_db),
):
    if not plan_mos_date or not plan_mos_date.strip():
        gmt7_now = datetime.now(timezone(timedelta(hours=7)))
        plan_mos_date = gmt7_now.strftime("%d %b %y")
    else:
        plan_mos_date = plan_mos_date.strip()

    stats = [
        {"status_delivery": status, "count": count}
        for status, count in get_dn_status_delivery_counts(
            db, lsp=lsp, plan_mos_date=plan_mos_date
        )
    ]
    total = sum(item["count"] for item in stats)
    return {"ok": True, "data": stats, "total": total}


@router.get("/list")
def list_dn_entries(
    dn_number: Optional[str] = Query(None, description="精确 DN number"),
    du_id: Optional[str] = Query(None, description="关联 DU ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if dn_number:
        dn_number = normalize_dn(dn_number)
        if not DN_RE.fullmatch(dn_number):
            raise HTTPException(status_code=400, detail=f"Invalid DN number: {dn_number}")
    if du_id:
        du_id = normalize_du(du_id)
        if not DU_RE.fullmatch(du_id):
            raise HTTPException(status_code=400, detail=f"Invalid DU ID: {du_id}")

    total, items = search_dn_list(
        db,
        dn_number=dn_number,
        du_id=du_id,
        page=page,
        page_size=page_size,
    )

    if not items:
        return {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [],
        }

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
        row["latest_record_created_at"] = to_gmt7_iso(
            latest.created_at if latest else None
        )
        data.append(row)

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": data,
    }


@router.get("/list/search")
def search_dn_list_endpoint(
    dn_number: Optional[str] = Query(None, description="精确 DN number"),
    du_id: Optional[str] = Query(None, description="关联 DU ID"),
    status_not_empty: Optional[bool] = Query(
        None, description="根据状态是否为空过滤 (true=非空, false=为空)"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if dn_number:
        dn_number = normalize_dn(dn_number)
        if not DN_RE.fullmatch(dn_number):
            raise HTTPException(status_code=400, detail=f"Invalid DN number: {dn_number}")
    if du_id:
        du_id = normalize_du(du_id)
        if not DU_RE.fullmatch(du_id):
            raise HTTPException(status_code=400, detail=f"Invalid DU ID: {du_id}")

    total, items = search_dn_list(
        db,
        dn_number=dn_number,
        du_id=du_id,
        status_not_empty=status_not_empty,
        page=page,
        page_size=page_size,
    )

    latest_records = get_latest_dn_records_map(db, [it.dn_number for it in items]) if items else {}
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
        row["latest_record_created_at"] = to_gmt7_iso(
            latest.created_at if latest else None
        )
        data.append(row)

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": data,
    }


@router.get("/records")
def list_dn_records_endpoint(
    dn_number: Optional[str] = Query(None, description="精确 DN number"),
    du_id: Optional[str] = Query(None, description="关联 DU ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if dn_number:
        dn_number = normalize_dn(dn_number)
        if not DN_RE.fullmatch(dn_number):
            raise HTTPException(status_code=400, detail=f"Invalid DN number: {dn_number}")
    if du_id:
        du_id = normalize_du(du_id)
        if not DU_RE.fullmatch(du_id):
            raise HTTPException(status_code=400, detail=f"Invalid DU ID: {du_id}")

    total, items = list_all_dn_records(
        db,
        dn_number=dn_number,
        du_id=du_id,
        page=page,
        page_size=page_size,
    )

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
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
        None,
        alias="dnnumber",
        description="重复 dn_number 或逗号分隔 (legacy alias)",
        include_in_schema=False,
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    try:
        dn_numbers = _normalize_batch_dn_numbers(dn_number, dnnumber_legacy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    total, items = list_dn_by_dn_numbers(
        db, dn_numbers, page=page, page_size=page_size
    )

    if not items:
        return {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [],
        }

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
        row["latest_record_created_at"] = to_gmt7_iso(
            latest.created_at if latest else None
        )
        data.append(row)

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": data,
    }


@router.get("/{dn_number}")
def get_dn_records(dn_number: str, db: Session = Depends(get_db)):
    dn_number = normalize_dn(dn_number)
    if not DN_RE.fullmatch(dn_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    items = list_dn_records(db, dn_number)
    return {
        "ok": True,
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
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


__all__ = [
    "batch_get_dn_records",
    "batch_search_dn_list",
    "get_dn_filters",
    "get_dn_records",
    "get_dn_status_delivery_stats",
    "list_dn_entries",
    "list_dn_records_endpoint",
    "search_dn_list_endpoint",
    "search_dn_records_api",
]
