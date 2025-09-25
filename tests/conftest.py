import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TEST_DB_PATH = ROOT / "test.db"
os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite:///{TEST_DB_PATH.as_posix()}?check_same_thread=false&sslmode=disable",
)
os.environ.setdefault("DN_SYNC_LOG_PATH", str(ROOT / "test_dn_sync.log"))
os.environ.setdefault("STORAGE_DISK_PATH", str(ROOT / "test_uploads"))
os.environ.setdefault("GOOGLE_API_KEY", "test-api-key")
os.environ.setdefault("GOOGLE_SHEET_URL", "https://example.com/test-sheet")
os.environ.setdefault("DN_SHEET_SYNC_INTERVAL_SECONDS", "60")

upload_dir = Path(os.environ["STORAGE_DISK_PATH"])
upload_dir.mkdir(parents=True, exist_ok=True)

if TEST_DB_PATH.exists():
    TEST_DB_PATH.unlink()

log_path = Path(os.environ["DN_SYNC_LOG_PATH"])
if log_path.exists():
    log_path.unlink()

from app.db import Base, SessionLocal, engine  # noqa: E402
from app import dn_columns  # noqa: E402
from app.dn_columns import refresh_dynamic_columns  # noqa: E402

if not hasattr(dn_columns, "extend_dn_table_columns"):
    dn_columns.extend_dn_table_columns = dn_columns.extend_dn_columns  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def prepare_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    refresh_dynamic_columns(engine)

    log_file = Path(os.environ["DN_SYNC_LOG_PATH"])
    if log_file.exists():
        log_file.unlink()

    yield


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


