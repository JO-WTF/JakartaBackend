from sqlalchemy.orm import Session
from .models import DU, DUUpdate

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
