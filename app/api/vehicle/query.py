"""Vehicle querying endpoints."""

from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.constants import VEHICLE_VALID_STATUSES
from app.core.sync import serialize_vehicle
from app.crud import get_vehicle_by_plate, list_vehicles
from app.db import get_db
from app.utils.string import normalize_vehicle_plate
from app.utils.time import TZ_GMT7

router = APIRouter(prefix="/api/vehicle")


@router.get("/vehicle")
def get_vehicle_info(vehicle_plate: str = Query(..., alias="vehiclePlate"), db: Session = Depends(get_db)):
    normalized_plate = normalize_vehicle_plate(vehicle_plate)
    if not normalized_plate:
        raise HTTPException(status_code=400, detail="vehicle_plate_required")

    vehicle = get_vehicle_by_plate(db, normalized_plate)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="vehicle_not_found")

    return {"ok": True, "vehicle": serialize_vehicle(vehicle)}


@router.get("/vehicles")
def list_vehicles_endpoint(
    status: str | None = Query(None),
    date: str | None = Query(None),
    db: Session = Depends(get_db),
):
    normalized_status: str | None = None
    if status:
        normalized_status = status.strip().lower()
        if normalized_status not in VEHICLE_VALID_STATUSES:
            raise HTTPException(status_code=400, detail="invalid_status")

    filter_by = "depart_time" if normalized_status == "departed" else "arrive_time"

    date_from = date_to = None
    if date:
        try:
            requested_date = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_date")

        start_local = datetime.combine(requested_date.date(), time(0, 0, 0, tzinfo=TZ_GMT7))
        end_local = datetime.combine(requested_date.date(), time(23, 59, 59, 999999, tzinfo=TZ_GMT7))
        date_from = start_local.astimezone(timezone.utc)
        date_to = end_local.astimezone(timezone.utc)

    vehicles = list_vehicles(db, status=normalized_status, filter_by=filter_by, date_from=date_from, date_to=date_to)
    return {"ok": True, "vehicles": [serialize_vehicle(vehicle) for vehicle in vehicles]}
