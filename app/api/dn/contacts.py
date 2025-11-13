"""DN contact lookup endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from app.constants import DN_RE
from app.services.dn_contacts import get_dn_contact_info

router = APIRouter(prefix="/api/dn")


@router.get(
    "/contacts/{dn_number}",
    summary="Lookup subcon contact by DN number",
    tags=["dn"],
)
async def get_dn_contact_endpoint(
    dn_number: str = Path(..., description="DN number to lookup"),
):
    normalized_dn = dn_number.strip()
    if not normalized_dn:
        raise HTTPException(status_code=400, detail="dn_number is required")
    if not DN_RE.match(normalized_dn):
        raise HTTPException(status_code=400, detail="Invalid dn_number")

    try:
        contact = await get_dn_contact_info(normalized_dn)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "data": {
            "contact_name": contact.contact_name,
            "contact_number": contact.contact_number,
        },
    }
