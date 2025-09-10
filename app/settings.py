from pydantic_settings import BaseSettings
from typing import List
import os

class Settings(BaseSettings):
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    allowed_origins: List[str] = []
    storage_driver: str = os.getenv("STORAGE_DRIVER", "disk")
    storage_disk_path: str = os.getenv("STORAGE_DISK_PATH", "/data/uploads")
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "")
    s3_region: str = os.getenv("S3_REGION", "")
    s3_bucket: str = os.getenv("S3_BUCKET", "")
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "")
    storage_base_url: str = os.getenv("STORAGE_BASE_URL", "")

settings = Settings()
raw = os.getenv("ALLOWED_ORIGINS", "")
if raw:
    settings.allowed_origins = [x.strip() for x in raw.split(",") if x.strip()]
else:
    settings.allowed_origins = ["*"]
