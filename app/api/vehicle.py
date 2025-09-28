"""Vehicle related API endpoints."""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.crud import (
    get_vehicle_by_plate,
    list_vehicles as list_vehicle_entries,
    mark_vehicle_departed,
    upsert_vehicle_signin,
)
from app.db import get_db
from app.models import Vehicle
from app.time_utils import TZ_GMT7, to_gmt7_iso

from .common import (
    VEHICLE_VALID_STATUSES,
    ensure_gmt7_timezone,
    normalize_vehicle_plate,
)

router = APIRouter(prefix="/api/vehicle", tags=["Vehicle"])


class VehicleSigninRequest(BaseModel):
    vehicle_plate: str = Field(..., alias="vehiclePlate")
    lsp: str = Field(..., alias="LSP")
    vehicle_type: str | None = Field(None, alias="vehicleType")
    driver_name: str | None = Field(None, alias="driverName")
    contact_number: str | None = Field(None, alias="contactNumber")
    arrive_time: datetime | None = Field(None, alias="arriveTime")

    class Config:
        populate_by_name = True


class VehicleDepartRequest(BaseModel):
    vehicle_plate: str = Field(..., alias="vehiclePlate")
    depart_time: datetime | None = Field(None, alias="departTime")

    class Config:
        populate_by_name = True


def serialize_vehicle(vehicle: Vehicle) -> dict[str, Any]:
    return {
        "vehiclePlate": vehicle.vehicle_plate,
        "vehicleType": vehicle.vehicle_type,
        "driverName": vehicle.driver_name,
        "contactNumber": vehicle.contact_number,
        "LSP": vehicle.lsp,
        "status": vehicle.status,
        "arriveTime": to_gmt7_iso(vehicle.arrive_time),
        "departTime": to_gmt7_iso(vehicle.depart_time),
        "createdAt": to_gmt7_iso(vehicle.created_at),
        "updatedAt": to_gmt7_iso(vehicle.updated_at),
    }


@router.post("/signin")
def vehicle_signin(
    payload: VehicleSigninRequest,
    db: Session = Depends(get_db),
):
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


@router.get("/vehicle")
def get_vehicle_info(
    vehicle_plate: str = Query(..., alias="vehiclePlate"),
    db: Session = Depends(get_db),
):
    normalized_plate = normalize_vehicle_plate(vehicle_plate)
    if not normalized_plate:
        raise HTTPException(status_code=400, detail="vehicle_plate_required")

    vehicle = get_vehicle_by_plate(db, normalized_plate)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="vehicle_not_found")

    return {"ok": True, "vehicle": serialize_vehicle(vehicle)}


@router.post("/depart")
def vehicle_depart(
    payload: VehicleDepartRequest,
    db: Session = Depends(get_db),
):
    vehicle_plate = normalize_vehicle_plate(payload.vehicle_plate)
    if not vehicle_plate:
        raise HTTPException(status_code=400, detail="vehicle_plate_required")

    depart_time = ensure_gmt7_timezone(payload.depart_time)

    vehicle = mark_vehicle_departed(
        db,
        vehicle_plate=vehicle_plate,
        depart_time=depart_time,
    )

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

        start_local = datetime.combine(
            requested_date.date(),
            time(0, 0, 0, tzinfo=TZ_GMT7),
        )
        end_local = datetime.combine(
            requested_date.date(),
            time(23, 59, 59, 999999, tzinfo=TZ_GMT7),
        )
        date_from = start_local.astimezone(timezone.utc)
        date_to = end_local.astimezone(timezone.utc)

    vehicles = list_vehicle_entries(
        db,
        status=normalized_status,
        filter_by=filter_by,
        date_from=date_from,
        date_to=date_to,
    )

    return {
        "ok": True,
        "vehicles": [serialize_vehicle(vehicle) for vehicle in vehicles],
    }
