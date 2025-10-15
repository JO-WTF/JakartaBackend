from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.pm import PMCreate, DNAction, DNQuery, PMInventoryQuery
from app import crud

router = APIRouter()


@router.post("/create-pm")
def create_pm(payload: PMCreate, db: Session = Depends(get_db)):
    """Create a PM entry."""
    pm_name_value = payload.pm_name

    pm = crud.create_pm(db, pm_name=pm_name_value, lng=payload.lng, lat=payload.lat)
    created = pm and pm.pm_name.lower() == pm_name_value.lower()
    return {"ok": True, "created": created, "pm": {"id": pm.id, "pm_name": pm.pm_name, "lng": pm.lng, "lat": pm.lat}}


@router.get("/list_pm")
def list_pm(db: Session = Depends(get_db)):
    """Return all PMs."""
    from app.models import PM

    items = db.query(PM).order_by(PM.pm_name.asc()).all()
    result = [{"id": pm.id, "pm_name": pm.pm_name, "lng": pm.lng, "lat": pm.lat} for pm in items]
    return {"ok": True, "total": len(result), "items": result}


@router.post("/inbound")
def dn_inbound(payload: DNAction, db: Session = Depends(get_db)):
    """Register a DN as inbound to a PM (create pm_inventory record)."""
    pm_name_value = payload.pm_name
    dn_norm = payload.dn_number

    try:
        rec = crud.pm_inbound(db, pm_name=pm_name_value, dn_number=dn_norm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "record": {"id": rec.id, "pm_name": rec.pm_name, "dn_number": rec.dn_number, "status": rec.status, "in_time": rec.in_time.isoformat()}}


@router.post("/outbound")
def dn_outbound(payload: DNAction, db: Session = Depends(get_db)):
    """Mark a DN as outbound from a PM (set status 'out' and out_time on latest in record)."""
    pm_name_value = payload.pm_name
    dn_norm = payload.dn_number

    # Find latest inventory record for this DN and PM that is not yet out
    try:
        rec = crud.pm_outbound(db, pm_name=pm_name_value, dn_number=dn_norm)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "ok": True,
        "record": {
            "id": rec.id,
            "pm_name": rec.pm_name,
            "dn_number": rec.dn_number,
            "status": rec.status,
            "out_time": rec.out_time.isoformat(),
        },
    }


@router.get("/find_dn")
def find_dn(query: DNQuery = Depends(), db: Session = Depends(get_db)):
    """Return the PM that currently holds the DN (latest in record not out)."""
    dn_norm = query.dn_number

    rec = crud.find_pm_by_dn(db, dn_number=dn_norm)
    if not rec:
        return {"ok": True, "pm": None}
    return {"ok": True, "pm": {"pm_name": rec.pm_name, "in_time": rec.in_time.isoformat() if rec.in_time else None}}


@router.get("/inventory")
def pm_inventory(query: PMInventoryQuery = Depends(), db: Session = Depends(get_db)):
    """Return all DN numbers currently in stock for the given PM."""
    pm_name_value = query.pm_name

    records = crud.list_pm_inventory(db, pm_name=pm_name_value)

    items: List[dict[str, Any]] = []
    for r in records:
        items.append({"id": r.id, "dn_number": r.dn_number, "in_time": r.in_time.isoformat() if r.in_time else None})

    return {"ok": True, "pm_name": pm_name_value, "total": len(items), "items": items}
