"""Vehicle-related schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

__all__ = ["VehicleSigninRequest", "VehicleDepartRequest"]


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
