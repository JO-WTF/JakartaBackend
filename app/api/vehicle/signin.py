"""Vehicle sign-in endpoint."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.sync import serialize_vehicle
from app.crud import upsert_vehicle_signin
from app.db import get_db
from app.schemas.vehicle import VehicleSigninRequest
from app.utils.string import normalize_vehicle_plate
from app.utils.time import ensure_gmt7_timezone

router = APIRouter(prefix="/api/vehicle")


@router.post("/signin")
def vehicle_signin(payload: VehicleSigninRequest, db: Session = Depends(get_db)):
    vehicle_plate = normalize_vehicle_plate(payload.vehicle_plate)
    if not vehicle_plate:
        raise HTTPException(status_code=400, detail="vehicle_plate_required")

    lsp = (payload.lsp or "").strip()
    if not lsp:
        raise HTTPException(status_code=400, detail="lsp_required")

    arrive_time = ensure_gmt7_timezone(payload.arrive_time)

    vehicle = upsert_vehicle_signin(
        db,
        vehicle_plate=vehicle_plate,
        lsp=lsp,
        vehicle_type=payload.vehicle_type,
        driver_name=payload.driver_name,
        contact_number=payload.contact_number,
        arrive_time=arrive_time,
    )

    return {"ok": True, "vehicle": serialize_vehicle(vehicle)}
