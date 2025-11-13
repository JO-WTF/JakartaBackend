"""Client utilities for the Huawei DN contact service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.settings import settings
from app.utils.logging import logger

__all__ = ["DNContactInfo", "get_dn_contact_info"]


@dataclass(slots=True)
class DNContactInfo:
    contact_name: str | None
    contact_number: str | None


def _normalize_contact_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return str(value)


def _extract_error_message(payload: Any) -> Optional[str]:
    if payload is None:
        return None
    if isinstance(payload, str):
        stripped = payload.strip()
        return stripped or None
    if isinstance(payload, dict):
        for key in ("detail", "message", "error", "msg", "description"):
            value = payload.get(key)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    return stripped
    return None


def _is_no_data_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip().lower() == "dn contact service returned no data":
        return True
    message = payload.get("message")
    if isinstance(message, str) and message.strip().lower() == "dn contact service returned no data":
        return True
    code = payload.get("code")
    if isinstance(code, str) and code.strip().upper() in {"NO_DATA", "NOT_FOUND"}:
        return True
    success = payload.get("success")
    if success is False and not payload.get("data"):
        return True
    return False


async def get_dn_contact_info(dn_number: str) -> DNContactInfo:
    """Fetch DN contact information from the Huawei API."""

    payload = {"dn_id": dn_number}
    headers = {
        "X-HW-ID": settings.dn_contacts_hw_id,
        "X-HW-APPKEY": settings.dn_contacts_app_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=settings.dn_contacts_timeout) as client:
            response = await client.post(
                settings.dn_contacts_api_url,
                json=payload,
                headers=headers,
            )
    except httpx.RequestError as exc:
        logger.exception("DN contact lookup failed: request error", extra={"dn_number": dn_number})
        raise RuntimeError("Unable to reach DN contact service") from exc

    if response.status_code >= 400:
        logger.warning(
            "DN contact lookup failed with HTTP %s",
            response.status_code,
            extra={"dn_number": dn_number, "body": response.text[:200]},
        )
        error_message: str | None = None
        try:
            error_message = _extract_error_message(response.json())
        except ValueError:
            error_message = _extract_error_message(response.text)
        message = error_message or f"DN contact service returned HTTP {response.status_code}"
        raise RuntimeError(message)

    try:
        data = response.json()
    except ValueError as exc:
        logger.exception("DN contact lookup failed: invalid JSON", extra={"dn_number": dn_number})
        raise RuntimeError("DN contact service returned malformed data") from exc

    if not isinstance(data, dict):
        logger.warning("DN contact lookup failed: unexpected payload", extra={"dn_number": dn_number})
        raise RuntimeError("DN contact service response is invalid")

    if _is_no_data_payload(data):
        logger.warning("DN contact lookup returned no data", extra={"dn_number": dn_number})
        message = _extract_error_message(data) or "DN contact service returned no data"
        raise RuntimeError(message)

    if not data.get("success"):
        logger.warning(
            "DN contact lookup failed: success flag missing/false",
            extra={"dn_number": dn_number, "payload": data},
        )
        message = _extract_error_message(data) or "DN contact service returned no data"
        raise RuntimeError(message)

    contact_data = data.get("data")
    if not isinstance(contact_data, dict) or not contact_data:
        logger.warning("DN contact lookup responded without contact data", extra={"dn_number": dn_number})
        message = _extract_error_message(data) or "DN contact service returned no data"
        raise RuntimeError(message)

    contact_name = _normalize_contact_value(contact_data.get("daily_work_owner"))
    contact_number = _normalize_contact_value(contact_data.get("subcon_contact"))

    return DNContactInfo(contact_name=contact_name, contact_number=contact_number)
