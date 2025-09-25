# 应用配置定义
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings
import os
from urllib.parse import urlparse

class Settings(BaseSettings):
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str | None = os.getenv("DATABASE_URL")  # 不给默认，缺失就暴露问题
    database_name: str | None = os.getenv("DATABASE_NAME")
    allowed_origins: str | list[str] = Field(default="*")
    storage_driver: str = os.getenv("STORAGE_DRIVER", "disk")
    storage_disk_path: str = os.getenv("STORAGE_DISK_PATH", "/data/uploads")
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "")
    s3_region: str = os.getenv("S3_REGION", "")
    s3_bucket: str = os.getenv("S3_BUCKET", "")
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "")
    storage_base_url: str = os.getenv("STORAGE_BASE_URL", "")
    google_api_key: str | None = os.getenv("GOOGLE_API_KEY")
    google_sheet_url: str | None = os.getenv("GOOGLE_SHEET_URL")
    dn_sheet_sync_interval_seconds: int = int(
        os.getenv("DN_SHEET_SYNC_INTERVAL_SECONDS", "300")
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value: str | list[str] | None) -> list[str]:
        if not value:
            return ["*"]

        if isinstance(value, str):
            if value.strip() == "*":
                return ["*"]
            items = [item.strip() for item in value.split(",") if item.strip()]
            return items or ["*"]

        if isinstance(value, list):
            items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
            return items or ["*"]

        raise TypeError("allowed_origins must be provided as a string or list of strings")

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            # 去除复制环境变量时常见的换行或多余空白
            value = value.strip()
            if value:
                value = value.replace("\n", "").replace("\r", "")
        return value


settings = Settings()

# 校正 DATABASE_URL（必须存在）
if not settings.database_url:
    raise RuntimeError("Missing env DATABASE_URL")

url = settings.database_url
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)
if "sslmode=" not in url:
    url += ("&" if "?" in url else "?") + "sslmode=require"
settings.database_url = url

if not settings.database_name:
    parsed = urlparse(url)
    db_path = parsed.path.lstrip("/")
    if parsed.scheme.startswith("sqlite"):
        settings.database_name = db_path or parsed.path
    else:
        settings.database_name = db_path or None
