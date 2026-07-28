"""Proxy endpoint for Find SG LPN upstream API."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.settings import settings
from app.utils.logging import logger

router = APIRouter(prefix="/api/find-sg-lpn-infos")


class FindSgLpnRequest(BaseModel):
    order_number: str = Field(..., min_length=1)
    pageSize: int = Field(1000, ge=1)
    pageNum: int = Field(1, ge=1)


def _read_json_response(response) -> dict:
    raw = response.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


@router.post("")
def find_sg_lpn_infos(payload: FindSgLpnRequest):
    if not settings.find_sg_lpn_appkey:
        raise HTTPException(status_code=503, detail="FIND_SG_LPN_APPKEY is not configured")

    body = {
        "order_number": payload.order_number.strip(),
        "pageSize": payload.pageSize,
        "pageNum": payload.pageNum,
    }
    if not body["order_number"]:
        raise HTTPException(status_code=400, detail="order_number is required")

    request = Request(
        settings.find_sg_lpn_url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "X-HW-ID": settings.find_sg_lpn_hw_id,
            "X-HW-APPKEY": settings.find_sg_lpn_appkey,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=settings.find_sg_lpn_timeout_seconds) as response:
            return _read_json_response(response)
    except HTTPError as exc:
        try:
            error_payload = _read_json_response(exc)
        except (json.JSONDecodeError, UnicodeDecodeError):
            error_payload = {"message": exc.reason}
        return JSONResponse(status_code=exc.code, content=error_payload)
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Find SG LPN upstream request failed: %s", exc)
        raise HTTPException(status_code=502, detail="Find SG LPN upstream request failed") from exc
