"""Vehicle departure endpoint."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.sync import serialize_vehicle
from app.crud import mark_vehicle_departed
from app.db import get_db
from app.schemas.vehicle import VehicleDepartRequest
from app.utils.string import normalize_vehicle_plate
from app.utils.time import ensure_gmt7_timezone

router = APIRouter(prefix="/api/vehicle")


@router.post("/depart")
def vehicle_depart(payload: VehicleDepartRequest, db: Session = Depends(get_db)):
    vehicle_plate = normalize_vehicle_plate(payload.vehicle_plate)
    if not vehicle_plate:
        raise HTTPException(status_code=400, detail="vehicle_plate_required")

    depart_time = ensure_gmt7_timezone(payload.depart_time)

    vehicle = mark_vehicle_departed(db, vehicle_plate=vehicle_plate, depart_time=depart_time)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="vehicle_not_found")

    return {"ok": True, "vehicle": serialize_vehicle(vehicle)}
