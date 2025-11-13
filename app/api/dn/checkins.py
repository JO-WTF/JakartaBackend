"""DN driver check-in endpoints."""

from __future__ import annotations
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.utils.logging import logger
from app.services.dn_checkins import DNCheckinError, create_dn_checkin

router = APIRouter(prefix="/api/dn")


class DNCheckinRequest(BaseModel):
    dn_id: str = Field(..., description="DN identifier")
    status: str = Field(..., description="Driver status")
    driver_name: str = Field(..., description="Driver name")
    driver_phone: str = Field(..., description="Driver phone number")
    check_in_time: str = Field(..., description="Check-in timestamp (YYYY-MM-DD HH:MM:SS)")
    longitude: str = Field(..., description="Longitude coordinate")
    latitude: str = Field(..., description="Latitude coordinate")

    class Config:
        extra = "forbid"


@router.post(
    "/checkins",
    summary="Create driver check-in",
    tags=["dn"],
)
async def create_checkin_endpoint(payload: DNCheckinRequest):
    try:
        response = await create_dn_checkin(payload.model_dump())
        logger.info(json.dumps(response))
    except DNCheckinError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return response
