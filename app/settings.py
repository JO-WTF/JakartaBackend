# app/settings.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
import os


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_parse_complex_value=False)

    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str | None = os.getenv("DATABASE_URL")  # 不给默认，缺失就暴露问题
    allowed_origins: list[str] | str = Field(default_factory=lambda: ["*"])
    storage_driver: str = os.getenv("STORAGE_DRIVER", "disk")
    storage_disk_path: str = os.getenv("STORAGE_DISK_PATH", "/data/uploads")
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "")
    s3_region: str = os.getenv("S3_REGION", "")
    s3_bucket: str = os.getenv("S3_BUCKET", "")
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "")
    storage_base_url: str = os.getenv("STORAGE_BASE_URL", "")
    google_api_key: str | None = os.getenv("GOOGLE_API_KEY")
    google_spreadsheet_url: str = os.getenv("GOOGLE_SPREADSHEET_URL", "")
    google_service_account_credentials: str | None = os.getenv("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS")
    mapbox_access_token: str | None = os.getenv("MAPBOX_ACCESS_TOKEN")

    @field_validator("allowed_origins", mode="after")
    @classmethod
    def _parse_allowed_origins(cls, value):
        """
        Accept comma-separated strings (common in .env files) in addition to JSON arrays.
        Defaults to wildcard (*) when empty.
        """
        if value is None or value == "":
            return ["*"]
        if isinstance(value, str):
            parsed = [part.strip() for part in value.split(",") if part.strip()]
            return parsed or ["*"]
        if isinstance(value, (list, tuple, set)):
            parsed = [str(part).strip() for part in value if str(part).strip()]
            return parsed or ["*"]
        return value


settings = Settings()

# 校正 DATABASE_URL（必须存在）
if not settings.database_url:
    raise RuntimeError("Missing env DATABASE_URL")

url = settings.database_url
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)

# Only enforce sslmode for Postgres connections; sqlite/local URLs do not support it
if url.split(":", 1)[0].startswith("postgres") and "sslmode=" not in url:
    url += ("&" if "?" in url else "?") + "sslmode=require"

settings.database_url = url
