from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.aging_orders import (
    update_pm_location_by_order_name,
    sync_aging_orders_sheet_to_db,
    run_pm_location_sheet_updates,
)
from app.db import get_db
from app.models import AgingOrder
from app.schemas.aging_order import AgingOrderPmUpdate, AgingOrderPmLocationQuery, AgingOrderQuery

router = APIRouter(prefix="/api/aging-orders")


def _serialize_order(row: AgingOrder) -> dict:
    return {
        "shipment_no": row.shipment_no,
        "order_name": row.order_name,
        "pm_location": row.pm_location,
        "shipment_status": row.shipment_status,
        "source_location": row.source_location,
        "destination_location": row.destination_location,
        "service_provider": row.service_provider,
        "sheet_title": row.sheet_title,
        "sheet_row": row.sheet_row,
        "sheet_cell": row.sheet_cell,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.post("/pm-location")
def update_pm_location(
    payload: AgingOrderPmUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Update PM Location for a given order_name (case-insensitive, trimmed)."""
    try:
        result = update_pm_location_by_order_name(
            db,
            order_name=payload.order_name,
            pm_name=payload.pm_location,
            skip_sheet_updates=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result.updated_count == 0 and not result.created:
        raise HTTPException(status_code=404, detail="order_name not found")

    background_tasks.add_task(
        run_pm_location_sheet_updates,
        order_name=payload.order_name,
        pm_value=payload.pm_location,
        created=result.created,
        shipment_no=result.shipment_no,
    )

    return {
        "ok": True,
        "updated": result.updated_count,
        "created": result.created,
        "shipment_no": result.shipment_no,
        "sheet_title": result.sheet_title,
        "sheet_row": result.sheet_row,
        "sheet_cell": result.sheet_cell,
        "sheet_sync_scheduled": True,
    }


@router.post("/sync")
def sync_aging_orders(db: Session = Depends(get_db)):
    """Manually sync Aging Orders Google Sheet into the database."""
    try:
        stats = sync_aging_orders_sheet_to_db(db)
    except Exception as exc:  # pragma: no cover - runtime sync errors
        raise HTTPException(status_code=500, detail=f"sync_failed: {exc}")

    return {
        "ok": True,
        "created": stats["created"],
        "updated": stats["updated"],
        "soft_deleted": stats.get("soft_deleted", 0),
        "total": stats["total"],
    }


@router.get("/by-order-name")
def get_aging_order(query: AgingOrderQuery = Depends(), db: Session = Depends(get_db)):
    """Fetch aging order rows by exact order_name (case-insensitive, trimmed)."""
    normalized_order = query.order_name.strip()
    rows = (
        db.query(AgingOrder)
        .filter(func.lower(func.trim(AgingOrder.order_name)) == normalized_order.lower())
        .filter(AgingOrder.is_deleted.is_(False))
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="order_name not found")

    items = [_serialize_order(row) for row in rows]

    return {"ok": True, "total": len(items), "items": items}


@router.get("/by-pm-location")
def get_aging_orders_by_pm_location(
    query: AgingOrderPmLocationQuery = Depends(), db: Session = Depends(get_db)
):
    """Fetch all aging orders under a specific PM location (case-insensitive, trimmed)."""
    normalized_pm = query.pm_location
    pm_candidates = {normalized_pm.lower()}
    if " " in normalized_pm:
        pm_candidates.add(normalized_pm.replace(" ", "+").lower())
        pm_candidates.add(normalized_pm.replace(" ", "%20").lower())
    rows = (
        db.query(AgingOrder)
        .filter(func.lower(func.trim(AgingOrder.pm_location)).in_(pm_candidates))
        .filter(AgingOrder.is_deleted.is_(False))
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="pm_location not found")

    items = [_serialize_order(row) for row in rows]

    return {"ok": True, "total": len(items), "items": items}


@router.get("/all")
def list_all_aging_orders(db: Session = Depends(get_db)):
    """List all aging orders (excluding soft-deleted rows)."""
    rows = (
        db.query(AgingOrder)
        .filter(AgingOrder.is_deleted.is_(False))
        .order_by(AgingOrder.updated_at.desc())
        .all()
    )
    items = [_serialize_order(row) for row in rows]
    return {"ok": True, "total": len(items), "items": items}
