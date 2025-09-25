# crud.py
from __future__ import annotations

import json
from typing import Any, Optional, Iterable, Tuple, List, Set, Dict, Sequence
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import and_, case, func, or_
from .models import DU, DURecord, DN, DNRecord, DNSyncLog
from .dn_columns import filter_assignable_dn_fields


def ensure_du(db: Session, du_id: str) -> DU:
    du = db.query(DU).filter(DU.du_id == du_id).one_or_none()
    if not du:
        du = DU(du_id=du_id)
        db.add(du)
        db.commit()
        db.refresh(du)
    return du

def add_record(db: Session, du_id: str, status: str, remark: str | None, photo_url: str | None, lng: str | None, lat: str | None) -> DURecord:
    rec = DURecord(du_id=du_id, status=status, remark=remark, photo_url=photo_url, lng=lng, lat=lat)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def list_records(db: Session, du_id: str, limit: int = 50) -> List[DURecord]:
    q = (
        db.query(DURecord)
        .filter(DURecord.du_id == du_id)
        .order_by(DURecord.created_at.desc())
        .limit(limit)
    )
    return q.all()

def search_records(
    db: Session,
    *,
    du_id: Optional[str] = None,
    status: Optional[str] = None,
    remark_keyword: Optional[str] = None,
    has_photo: Optional[bool] = None,
    date_from=None,
    date_to=None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[int, List[DURecord]]:
    base_q = db.query(DURecord)
    conds = []
    if du_id:
        conds.append(DURecord.du_id == du_id)
    if status:
        conds.append(DURecord.status == status)
    if remark_keyword:
        conds.append(DURecord.remark.ilike(f"%{remark_keyword}%"))
    if has_photo is True:
        conds.append(DURecord.photo_url.isnot(None))
    elif has_photo is False:
        conds.append(DURecord.photo_url.is_(None))
    if date_from is not None:
        conds.append(DURecord.created_at >= date_from)
    if date_to is not None:
        conds.append(DURecord.created_at <= date_to)
    if conds:
        base_q = base_q.filter(and_(*conds))

    # 统计总数（基于未分页查询）
    total = base_q.count()

    # 分页数据
    items = (
        base_q.order_by(DURecord.created_at.desc(), DURecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return total, items

def get_record_by_id(db: Session, rec_id: int) -> Optional[DURecord]:
    """按主键 ID 获取单条记录"""
    return db.query(DURecord).get(rec_id)  # SQLAlchemy 1.x 风格；2.x 仍兼容

def update_record(
    db: Session,
    rec_id: int,
    *,
    status: Optional[str] = None,
    remark: Optional[str] = None,
    photo_url: Optional[str] = None,
) -> Optional[DURecord]:
    """
    修改一条更新记录：仅更新传入的字段（None 表示不修改）
    """
    obj = db.query(DURecord).get(rec_id)
    if not obj:
        return None

    if status is not None:
        obj.status = status
    if remark is not None:
        obj.remark = remark
    if photo_url is not None:
        obj.photo_url = photo_url

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj

def delete_record(db: Session, rec_id: int) -> bool:
    """
    删除一条更新记录
    """
    obj = db.query(DURecord).get(rec_id)
    if not obj:
        return False
    db.delete(obj)
    db.commit()
    return True


def list_records_by_du_ids(
    db: Session,
    du_ids: Iterable[str],
    *,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[int, List[DURecord]]:
    """
    批量查询多个 DU 的更新记录，按时间倒序（其次按 id 倒序），支持分页。
    """
    du_ids = [x for x in {x for x in du_ids if x}]
    if not du_ids:
        return 0, []

    base_q = db.query(DURecord).filter(DURecord.du_id.in_(du_ids))

    total = base_q.count()
    items = (
        base_q.order_by(DURecord.created_at.desc(), DURecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return total, items


def get_existing_du_ids(db: Session, du_ids: Iterable[str]) -> Set[str]:
    """批量查询数据库中已存在的 DU ID 列表"""

    unique_ids = {du_id for du_id in du_ids if du_id}
    if not unique_ids:
        return set()

    rows = db.query(DU.du_id).filter(DU.du_id.in_(unique_ids)).all()
    return {row[0] for row in rows}


def ensure_dn(db: Session, dn_number: str, **fields: str | None) -> DN:
    assignable = filter_assignable_dn_fields(fields)
    non_null_assignable = {k: v for k, v in assignable.items() if v is not None}

    dn = db.query(DN).filter(DN.dn_number == dn_number).one_or_none()
    if not dn:
        dn = DN(dn_number=dn_number, **non_null_assignable)
        db.add(dn)
        db.commit()
        db.refresh(dn)
        return dn

    updated = False
    for key, value in non_null_assignable.items():
        if getattr(dn, key, None) != value:
            setattr(dn, key, value)
            updated = True

    if updated:
        db.add(dn)
        db.commit()
        db.refresh(dn)

    return dn


def delete_dn(db: Session, dn_number: str) -> bool:
    dn = db.query(DN).filter(DN.dn_number == dn_number).one_or_none()
    if not dn:
        return False

    db.query(DNRecord).filter(DNRecord.dn_number == dn_number).delete(
        synchronize_session=False
    )
    db.delete(dn)
    db.commit()
    return True


def add_dn_record(
    db: Session,
    dn_number: str,
    status: str,
    remark: str | None,
    photo_url: str | None,
    lng: str | None,
    lat: str | None,
    du_id: str | None = None,
) -> DNRecord:
    rec = DNRecord(
        dn_number=dn_number,
        du_id=du_id,
        status=status,
        remark=remark,
        photo_url=photo_url,
        lng=lng,
        lat=lat,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    # Keep the DN table in sync with the latest record that was just created.
    ensure_dn(
        db,
        dn_number,
        du_id=du_id,
        status=status,
        remark=remark,
        photo_url=photo_url,
        lng=lng,
        lat=lat,
    )
    db.refresh(rec)
    return rec


def create_dn_sync_log(
    db: Session,
    *,
    status: str,
    synced_numbers: Iterable[str] | None = None,
    message: Optional[str] = None,
    error_message: Optional[str] = None,
    error_traceback: Optional[str] = None,
) -> DNSyncLog:
    numbers_list = sorted({str(num) for num in (synced_numbers or []) if str(num)})
    log = DNSyncLog(
        status=status,
        synced_count=len(numbers_list),
        dn_numbers_json=json.dumps(numbers_list) if numbers_list else None,
        message=message,
        error_message=error_message,
        error_traceback=error_traceback,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_latest_dn_sync_log(db: Session) -> Optional[DNSyncLog]:
    return (
        db.query(DNSyncLog)
        .order_by(DNSyncLog.created_at.desc(), DNSyncLog.id.desc())
        .first()
    )


def list_dn_records(db: Session, dn_number: str, limit: int = 50) -> List[DNRecord]:
    q = (
        db.query(DNRecord)
        .filter(DNRecord.dn_number == dn_number)
        .order_by(DNRecord.created_at.desc())
        .limit(limit)
    )
    return q.all()


def list_all_dn_records(db: Session) -> List[DNRecord]:
    return (
        db.query(DNRecord)
        .order_by(DNRecord.created_at.desc(), DNRecord.id.desc())
        .all()
    )


def search_dn_records(
    db: Session,
    *,
    dn_number: Optional[str] = None,
    du_id: Optional[str] = None,
    status: Optional[str] = None,
    remark_keyword: Optional[str] = None,
    has_photo: Optional[bool] = None,
    date_from=None,
    date_to=None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[int, List[DNRecord]]:
    base_q = db.query(DNRecord)
    conds = []
    if dn_number:
        conds.append(DNRecord.dn_number == dn_number)
    if du_id:
        conds.append(DNRecord.du_id == du_id)
    if status:
        conds.append(DNRecord.status == status)
    if remark_keyword:
        conds.append(DNRecord.remark.ilike(f"%{remark_keyword}%"))
    if has_photo is True:
        conds.append(DNRecord.photo_url.isnot(None))
    elif has_photo is False:
        conds.append(DNRecord.photo_url.is_(None))
    if date_from is not None:
        conds.append(DNRecord.created_at >= date_from)
    if date_to is not None:
        conds.append(DNRecord.created_at <= date_to)
    if conds:
        base_q = base_q.filter(and_(*conds))

    total = base_q.count()
    items = (
        base_q.order_by(DNRecord.created_at.desc(), DNRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return total, items


def get_dn_record_by_id(db: Session, rec_id: int) -> Optional[DNRecord]:
    return db.query(DNRecord).get(rec_id)


def update_dn_record(
    db: Session,
    rec_id: int,
    *,
    status: Optional[str] = None,
    remark: Optional[str] = None,
    photo_url: Optional[str] = None,
    du_id: Optional[str] = None,
    du_id_set: bool = False,
) -> Optional[DNRecord]:
    obj = db.query(DNRecord).get(rec_id)
    if not obj:
        return None

    if status is not None:
        obj.status = status
    if remark is not None:
        obj.remark = remark
    if photo_url is not None:
        obj.photo_url = photo_url
    if du_id_set:
        obj.du_id = du_id
    elif du_id is not None:
        obj.du_id = du_id

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete_dn_record(db: Session, rec_id: int) -> bool:
    obj = db.query(DNRecord).get(rec_id)
    if not obj:
        return False
    db.delete(obj)
    db.commit()
    return True


def list_dn_records_by_dn_numbers(
    db: Session,
    dn_numbers: Iterable[str],
    *,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[int, List[DNRecord]]:
    dn_numbers = [x for x in {x for x in dn_numbers if x}]
    if not dn_numbers:
        return 0, []

    base_q = db.query(DNRecord).filter(DNRecord.dn_number.in_(dn_numbers))

    total = base_q.count()
    items = (
        base_q.order_by(DNRecord.created_at.desc(), DNRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return total, items


def list_dn_by_dn_numbers(
    db: Session,
    dn_numbers: Iterable[str],
    *,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[int, List[DN]]:
    numbers = [number for number in dict.fromkeys(dn_numbers) if number]
    if not numbers:
        return 0, []

    base_q = db.query(DN).filter(DN.dn_number.in_(numbers))

    total = base_q.count()

    ordering = case(
        *[(number, index) for index, number in enumerate(numbers)],
        value=DN.dn_number,
        else_=len(numbers),
    )

    items = (
        base_q.order_by(ordering)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return total, items


def get_existing_dn_numbers(db: Session, dn_numbers: Iterable[str]) -> Set[str]:
    unique_numbers = {dn_number for dn_number in dn_numbers if dn_number}
    if not unique_numbers:
        return set()

    rows = db.query(DN.dn_number).filter(DN.dn_number.in_(unique_numbers)).all()
    return {row[0] for row in rows}


def get_latest_dn_records_map(db: Session, dn_numbers: Iterable[str]) -> Dict[str, DNRecord]:
    unique_numbers = [number for number in {number for number in dn_numbers if number}]
    if not unique_numbers:
        return {}

    q = (
        db.query(DNRecord)
        .filter(DNRecord.dn_number.in_(unique_numbers))
        .order_by(DNRecord.dn_number.asc(), DNRecord.created_at.desc(), DNRecord.id.desc())
    )

    latest: Dict[str, DNRecord] = {}
    for rec in q:
        key = rec.dn_number
        if key not in latest:
            latest[key] = rec
            if len(latest) == len(unique_numbers):
                break
    return latest


def search_dn_list(
    db: Session,
    *,
    plan_mos_dates: Sequence[str] | None = None,
    dn_number: str | None = None,
    du_id: str | None = None,
    status_values: Sequence[str] | None = None,
    status_delivery_values: Sequence[str] | None = None,
    status_not_empty: bool | None = None,
    has_coordinate: bool | None = None,
    lsp_values: Sequence[str] | None = None,
    region_values: Sequence[str] | None = None,
    area: str | None = None,
    status_wh_values: Sequence[str] | None = None,
    subcon_values: Sequence[str] | None = None,
    project_request: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[int, List[DN]]:
    base_q = db.query(DN)
    conds = []

    trimmed_plan_mos_dates = [
        value.strip()
        for value in (plan_mos_dates or [])
        if isinstance(value, str) and value.strip()
    ]
    if trimmed_plan_mos_dates:
        conds.append(func.trim(DN.plan_mos_date).in_(trimmed_plan_mos_dates))
    if dn_number:
        conds.append(DN.dn_number == dn_number)
    if du_id:
        conds.append(DN.du_id == du_id)
    normalized_status_values = [
        value.strip().lower()
        for value in (status_values or [])
        if isinstance(value, str) and value.strip()
    ]
    if normalized_status_values:
        conds.append(func.lower(func.trim(DN.status)).in_(normalized_status_values))
    normalized_status_delivery = [
        value.strip().lower()
        for value in (status_delivery_values or [])
        if isinstance(value, str) and value.strip()
    ]
    if normalized_status_delivery:
        conds.append(
            func.lower(func.trim(DN.status_delivery)).in_(normalized_status_delivery)
        )
    if status_not_empty is True:
        conds.append(
            and_(
                DN.status.isnot(None),
                func.length(func.trim(DN.status)) > 0,
            )
        )
    elif status_not_empty is False:
        conds.append(
            or_(
                DN.status.is_(None),
                func.length(func.trim(DN.status)) == 0,
            )
        )
    if has_coordinate is True:
        conds.append(
            and_(
                DN.lat.isnot(None),
                func.length(func.trim(DN.lat)) > 0,
                DN.lng.isnot(None),
                func.length(func.trim(DN.lng)) > 0,
            )
        )
    elif has_coordinate is False:
        conds.append(
            or_(
                DN.lat.is_(None),
                DN.lng.is_(None),
                func.length(func.trim(DN.lat)) == 0,
                func.length(func.trim(DN.lng)) == 0,
            )
        )
    trimmed_lsp_values = [
        value.strip()
        for value in (lsp_values or [])
        if isinstance(value, str) and value.strip()
    ]
    if trimmed_lsp_values:
        conds.append(func.trim(DN.lsp).in_(trimmed_lsp_values))
    trimmed_region_values = [
        value.strip()
        for value in (region_values or [])
        if isinstance(value, str) and value.strip()
    ]
    if trimmed_region_values:
        conds.append(func.trim(DN.region).in_(trimmed_region_values))
    if area:
        conds.append(DN.area == area)
    trimmed_status_wh_values = [
        value.strip()
        for value in (status_wh_values or [])
        if isinstance(value, str) and value.strip()
    ]
    if trimmed_status_wh_values:
        conds.append(func.trim(DN.status_wh).in_(trimmed_status_wh_values))
    trimmed_subcon_values = [
        value.strip()
        for value in (subcon_values or [])
        if isinstance(value, str) and value.strip()
    ]
    if trimmed_subcon_values:
        conds.append(func.trim(DN.subcon).in_(trimmed_subcon_values))
    if project_request:
        conds.append(DN.project_request == project_request)

    if conds:
        base_q = base_q.filter(and_(*conds))

    total = base_q.count()
    items = (
        base_q.order_by(DN.created_at.desc(), DN.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return total, items


def get_dn_unique_field_values(db: Session) -> Tuple[Dict[str, List[str]], int]:
    """Return unique DN field values for filter options along with total count."""

    columns: Dict[str, Any] = {
        "lsp": DN.lsp,
        "region": DN.region,
        "plan_mos_date": DN.plan_mos_date,
        "subcon": DN.subcon,
        "status_wh": DN.status_wh,
        "status_delivery": DN.status_delivery,
    }

    distinct_values: Dict[str, List[str]] = {}

    for key, column in columns.items():
        trimmed = func.trim(column).label("value")
        query = (
            db.query(trimmed)
            .filter(column.isnot(None))
            .filter(func.length(trimmed) > 0)
            .distinct()
            .order_by(trimmed.asc())
        )
        values = [row.value for row in query.all() if row.value]

        if key == "plan_mos_date":
            values = _sort_plan_mos_dates_desc(values)

        distinct_values[key] = values

    total = db.query(func.count(DN.id)).scalar() or 0

    return distinct_values, int(total)


def _sort_plan_mos_dates_desc(values: List[str]) -> List[str]:
    """Sort plan_mos_date values descending by parsed date when possible."""

    def _parse(value: str) -> datetime | None:
        formats = [
            "%d %b %y",
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%Y/%m/%d",
            "%d/%m/%Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None

    return sorted(
        values,
        key=lambda v: (_parse(v) or datetime.min, v),
        reverse=True,
    )


def get_dn_status_delivery_counts(
    db: Session,
    *,
    lsp: Optional[str] = None,
    plan_mos_date: Optional[str] = None,
) -> List[tuple[str, int]]:
    """Return DN counts grouped by status_delivery with optional filtering."""

    status_expr = func.coalesce(
        func.nullif(func.trim(DN.status_delivery), ""), "NO STATUS"
    )

    query = db.query(
        status_expr.label("status_delivery"), func.count(DN.id).label("count")
    )

    trimmed_lsp = lsp.strip() if lsp else None
    if trimmed_lsp:
        query = query.filter(func.trim(DN.lsp) == trimmed_lsp)

    trimmed_plan_mos_date = plan_mos_date.strip() if plan_mos_date else None
    if trimmed_plan_mos_date:
        query = query.filter(func.trim(DN.plan_mos_date) == trimmed_plan_mos_date)

    rows = (
        query.group_by(status_expr)
        .order_by(status_expr.asc())
        .all()
    )

    return [(row.status_delivery, int(row.count)) for row in rows]
