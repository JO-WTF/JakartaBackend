# app/settings.py
from __future__ import annotations

import json
import os
from typing import Iterable, List

from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str | None = os.getenv("DATABASE_URL")  # 不给默认，缺失就暴露问题
    allowed_origins: list[str] = []
    storage_driver: str = os.getenv("STORAGE_DRIVER", "disk")
    storage_disk_path: str = os.getenv("storage_DISK_PATH", "/data/uploads")
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "")
    s3_region: str = os.getenv("S3_REGION", "")
    s3_bucket: str = os.getenv("S3_BUCKET", "")
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "")
    storage_base_url: str = os.getenv("STORAGE_BASE_URL", "")
    dn_sheet_columns: List[str] = Field(default_factory=list)

settings = Settings()

raw = os.getenv("ALLOWED_ORIGINS", "")
settings.allowed_origins = [x.strip() for x in raw.split(",") if x.strip()] or ["*"]


def _parse_dn_sheet_columns(raw_value: str) -> List[str]:
    """Parse DN sheet columns from environment configuration."""

    if not raw_value:
        return []

    raw_value = raw_value.strip()
    if not raw_value:
        return []

    parsed: Iterable[str]
    try:
        loaded = json.loads(raw_value)
    except json.JSONDecodeError:
        parsed = raw_value.split(",")
    else:
        if isinstance(loaded, str):
            parsed = [loaded]
        elif isinstance(loaded, Iterable):
            parsed = [str(item) for item in loaded]
        else:
            parsed = []

    result: List[str] = []
    seen: set[str] = set()
    for item in parsed:
        column = str(item).strip()
        if not column:
            continue
        lower = column.lower()
        if lower in seen:
            continue
        seen.add(lower)
        result.append(column)
    return result


settings.dn_sheet_columns = _parse_dn_sheet_columns(os.getenv("DN_SHEET_COLUMNS", ""))

# 校正 DATABASE_URL（必须存在）
if not settings.database_url:
    raise RuntimeError("Missing env DATABASE_URL")

url = settings.database_url
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)
if "sslmode=" not in url:
    url += ("&" if "?" in url else "?") + "sslmode=require"
settings.database_url = url
