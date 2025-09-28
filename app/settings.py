"""Application settings management."""

from __future__ import annotations

from pydantic import AliasChoices, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load settings from environment variables and a ``.env`` file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = Field(default="development")
    database_url: str = Field(alias="DATABASE_URL")
    allowed_origins: str = Field(default="", alias="ALLOWED_ORIGINS")
    storage_driver: str = Field(default="disk", alias="STORAGE_DRIVER")
    storage_disk_path: str = Field(
        default="/data/uploads",
        validation_alias=AliasChoices("STORAGE_DISK_PATH", "storage_DISK_PATH"),
    )
    s3_endpoint: str = Field(default="", alias="S3_ENDPOINT")
    s3_region: str = Field(default="", alias="S3_REGION")
    s3_bucket: str = Field(default="", alias="S3_BUCKET")
    s3_access_key: str = Field(default="", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field(default="", alias="S3_SECRET_KEY")
    storage_base_url: str = Field(default="", alias="STORAGE_BASE_URL")
    google_service_account_credentials: str = Field(
        alias="GOOGLE_SERVICE_ACCOUNT_CREDENTIALS"
    )
    google_spreadsheet_url: str = Field(alias="GOOGLE_SPREADSHEET_URL")


try:
    settings = Settings()
except ValidationError as exc:
    missing_envs = set()
    env_field_aliases = {
        "database_url": "DATABASE_URL",
        "google_service_account_credentials": "GOOGLE_SERVICE_ACCOUNT_CREDENTIALS",
        "google_spreadsheet_url": "GOOGLE_SPREADSHEET_URL",
    }
    for error in exc.errors():
        if error.get("type") not in {"missing", "field_required"}:
            continue
        loc = error.get("loc") or ()
        if not loc:
            continue
        field_name = loc[-1]
        env_name = env_field_aliases.get(field_name, str(field_name))
        missing_envs.add(env_name)

    if missing_envs:
        missing_list = ", ".join(sorted(missing_envs))
        raise RuntimeError(f"Missing env variables: {missing_list}") from exc
    raise

raw_allowed = settings.allowed_origins
settings.allowed_origins = [x.strip() for x in raw_allowed.split(",") if x.strip()] or ["*"]

url = settings.database_url
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)
if "sslmode=" not in url:
    url += ("&" if "?" in url else "?") + "sslmode=require"
settings.database_url = url
