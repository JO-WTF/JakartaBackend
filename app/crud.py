from sqlalchemy.orm import Session
from .models import DU, DUUpdate
from sqlalchemy import and_
from typing import Optional

def ensure_du(db: Session, du_id: str) -> DU:
    du = db.query(DU).filter(DU.du_id == du_id).one_or_none()
    if not du:
        du = DU(du_id=du_id)
        db.add(du)
        db.commit()
        db.refresh(du)
    return du

def add_update(db: Session, du_id: str, status: str, remark: str | None, photo_url: str | None) -> DUUpdate:
    rec = DUUpdate(du_id=du_id, status=status, remark=remark, photo_url=photo_url)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec

def list_updates(db: Session, du_id: str, limit: int = 50):
    q = db.query(DUUpdate).filter(DUUpdate.du_id == du_id).order_by(DUUpdate.created_at.desc()).limit(limit)
    return q.all()

def search_updates(
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
):
    q = db.query(DUUpdate)
    conds = []
    if du_id:
        conds.append(DUUpdate.du_id == du_id)
    if status:
        conds.append(DUUpdate.status == status)
    if remark_keyword:
        conds.append(DUUpdate.remark.ilike(f"%{remark_keyword}%"))
    if has_photo is True:
        conds.append(DUUpdate.photo_url.isnot(None))
    elif has_photo is False:
        conds.append(DUUpdate.photo_url.is_(None))
    if date_from is not None:
        conds.append(DUUpdate.created_at >= date_from)
    if date_to is not None:
        conds.append(DUUpdate.created_at <= date_to)
    if conds:
        q = q.filter(and_(*conds))

    total = q.count()
    items = q.order_by(DUUpdate.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return total, items
