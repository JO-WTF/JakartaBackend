"""Client helpers for posting DN driver check-ins to the Huawei API."""

from __future__ import annotations

from typing import Any, Mapping

import httpx

from app.settings import settings
from app.utils.logging import logger

__all__ = ["DNCheckinError", "create_dn_checkin"]


class DNCheckinError(RuntimeError):
    """Raised when the Huawei DN check-in service cannot process a request."""


async def create_dn_checkin(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Relay the provided payload to the Huawei DN check-in API."""

    if not settings.dn_checkin_api_switch:
        logger.warning("DN check-in service api switch is False")
        raise DNCheckinError("DN check-in service api switch is False")

    headers = {
        "X-HW-ID": settings.dn_contacts_hw_id,
        "X-HW-APPKEY": settings.dn_contacts_app_key,
        "Content-Type": "application/json",
    }

    try:
        logger.info(settings.dn_checkins_api_url)
        async with httpx.AsyncClient(timeout=settings.dn_contacts_timeout) as client:
            response = await client.post(
                settings.dn_checkins_api_url,
                json=dict(payload),
                headers=headers,
            )
    except httpx.RequestError as exc:
        logger.exception("DN check-in request failed", extra={"payload": payload})
        raise DNCheckinError("Unable to reach DN check-in service") from exc

    if response.status_code >= 400:
        logger.warning(
            "DN check-in rejected with status %s",
            response.status_code,
            extra={"body": response.text[:200]},
        )
        raise DNCheckinError("DN check-in service returned an error")

    try:
        data = response.json()
    except ValueError as exc:
        logger.exception("DN check-in service returned invalid JSON")
        raise DNCheckinError("DN check-in service returned malformed data") from exc

    if not isinstance(data, dict):
        logger.warning("DN check-in service response is not an object", extra={"payload": data})
        raise DNCheckinError("DN check-in service response is invalid")

    if not data.get("success"):
        logger.warning("DN check-in service reported failure", extra={"response": data})
        raise DNCheckinError("DN check-in service rejected the request")

    return data
