"""Test timestamp update functionality for DN status changes."""

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set required environment variables before importing app modules
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test?sslmode=require")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS", "{}")
os.environ.setdefault("STORAGE_DISK_PATH", "/tmp/test_uploads")

from app.core.sheet import sync_delivery_status_to_sheet, sync_status_timestamp_to_sheet  # noqa: E402
from app.utils.time import TZ_GMT7  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import DN, DNRecord  # noqa: E402
from app.api.dn.update import update_dn  # noqa: E402
from app.dn_columns import ensure_dynamic_columns_loaded, get_sheet_columns  # noqa: E402


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestingSessionLocal()
    ensure_dynamic_columns_loaded(session)
    try:
        yield session
    finally:
        session.close()
