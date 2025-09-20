# crud.py
from __future__ import annotations

from typing import Optional, Iterable, Tuple, List, Set, Dict
from sqlalchemy.orm import Session
from sqlalchemy import and_
from .models import DU, DURecord, DN, DNRecord


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
    dn = db.query(DN).filter(DN.dn_number == dn_number).one_or_none()
    if not dn:
        dn = DN(dn_number=dn_number, **{k: v for k, v in fields.items() if v is not None})
        db.add(dn)
        db.commit()
        db.refresh(dn)
        return dn

    updated = False
    for key, value in fields.items():
        if value is None:
            continue
        if getattr(dn, key, None) != value:
            setattr(dn, key, value)
            updated = True

    if updated:
        db.add(dn)
        db.commit()
        db.refresh(dn)

    return dn


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


def list_dn_records(db: Session, dn_number: str, limit: int = 50) -> List[DNRecord]:
    q = (
        db.query(DNRecord)
        .filter(DNRecord.dn_number == dn_number)
        .order_by(DNRecord.created_at.desc())
        .limit(limit)
    )
    return q.all()


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
