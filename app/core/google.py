"""Google authentication helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import gspread

from app.settings import settings
from app.utils.logging import logger

__all__ = ["create_gspread_client", "SPREADSHEET_URL", "GS_KEY_PATH"]

GS_KEY_PATH = Path("/etc/secrets/gskey.json")
SPREADSHEET_URL = settings.google_spreadsheet_url

_SERVICE_ACCOUNT_INFO: dict[str, Any] | None = None


def _load_service_account_info() -> dict[str, Any]:
    """Load Google service account credentials from env or filesystem."""
    global _SERVICE_ACCOUNT_INFO
    if _SERVICE_ACCOUNT_INFO is not None:
        return _SERVICE_ACCOUNT_INFO

    raw_credentials = settings.google_service_account_credentials
    source_desc = "environment variable GOOGLE_SERVICE_ACCOUNT_CREDENTIALS"
    credentials_from_env = bool(raw_credentials)

    if not raw_credentials:
        source_desc = f"file {GS_KEY_PATH}"
        if not GS_KEY_PATH.exists():
            raise RuntimeError(
                "Missing Google service account credentials. Set GOOGLE_SERVICE_ACCOUNT_CREDENTIALS or provide /etc/secrets/gskey.json."
            )
        try:
            raw_credentials = GS_KEY_PATH.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise RuntimeError("Missing Google service account credentials file at /etc/secrets/gskey.json.") from exc
        except OSError as exc:
            raise RuntimeError(
                f"Failed to read Google service account credentials from {GS_KEY_PATH}: {exc}"
            ) from exc

    try:
        info = json.loads(raw_credentials)
    except json.JSONDecodeError as exc:
        if credentials_from_env:
            logger.error("Invalid JSON for GOOGLE_SERVICE_ACCOUNT_CREDENTIALS: %s", raw_credentials)
        raise RuntimeError("Invalid JSON for Google service account credentials") from exc

    _SERVICE_ACCOUNT_INFO = info
    logger.info("Loaded Google service account credentials from %s", source_desc)
    return info


def create_gspread_client() -> gspread.Client:
    """Create a gspread client using configured credentials."""
    service_account_info = _load_service_account_info()
    logger.debug("Creating gspread client using configured service account credentials")
    try:
        gc = gspread.service_account_from_dict(service_account_info)
    except Exception as exc:  # pragma: no cover - gspread failure surfaces as runtime error
        logger.exception("Failed to authenticate using Google service account credentials: %s", exc)
        raise

    logger.info("Using gspread service account authentication")
    return gc
