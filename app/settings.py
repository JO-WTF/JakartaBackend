# app/settings.py
from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str | None = os.getenv("DATABASE_URL")  # 不给默认，缺失就暴露问题
    allowed_origins: list[str] = []
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

settings = Settings()

raw = os.getenv("ALLOWED_ORIGINS", "")
settings.allowed_origins = [x.strip() for x in raw.split(",") if x.strip()] or ["*"]

# 校正 DATABASE_URL（必须存在）
if not settings.database_url:
    raise RuntimeError("Missing env DATABASE_URL")

url = settings.database_url
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql://", 1)
if "sslmode=" not in url:
    url += ("&" if "?" in url else "?") + "sslmode=require"
settings.database_url = url
