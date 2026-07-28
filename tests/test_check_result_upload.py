import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("DN_CONTACTS_API_BASE_URL", "https://example.test")
os.environ.setdefault("DN_CONTACTS_HW_ID", "test-hw-id")
os.environ.setdefault("DN_CONTACTS_APP_KEY", "test-app-key")

from app.db import Base, SessionLocal, get_db
from app.main import app
from app.models import CheckResult


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
    )

    original_session_factory = SessionLocal
    app.dependency_overrides.clear()
    from app import db as app_db
    app_db.engine = engine
    app_db.SessionLocal = TestingSessionLocal
    app_db.Base.metadata.create_all(engine)

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        app_db.engine = original_session_factory.kw["bind"] if hasattr(original_session_factory, "kw") else app_db.engine
        app_db.SessionLocal = original_session_factory


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_upload_check_result_persists_payload(db_session: Session, client):
    payload = {
        "reportId": "report-123",
        "dnNumber": "TESTDN12345678",
        "lsp": "LSP01",
        "checkerName": "Alice",
        "checkTime": "2026-07-28 10:00:00",
        "boxCount": 2,
        "checkedCount": 1,
        "status": "partial",
        "boxes": [
            {"boxNo": "BOX1", "itemNo": "ITEM1", "qty": "1", "status": "Checked"},
            {"boxNo": "BOX2", "itemNo": "ITEM2", "qty": "2", "status": "Pending"},
        ],
        "metadata": {"generatedAt": "2026-07-28T10:00:00Z"},
    }

    response = client.post("/api/dn/check_result", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["report_id"] == "report-123"
    assert data["dn_number"] == "TESTDN12345678"
    assert data["box_count"] == 2
    assert data["checked_count"] == 1

    stored = db_session.query(CheckResult).filter(CheckResult.report_id == "report-123").one()
    assert stored.dn_number == "TESTDN12345678"
    assert stored.lsp == "LSP01"
    assert stored.checker_name == "Alice"
    assert stored.box_count == 2
    assert stored.checked_count == 1
    assert json.loads(stored.boxes_json)[0]["boxNo"] == "BOX1"
    assert json.loads(stored.metadata_json)["generatedAt"] == "2026-07-28T10:00:00Z"
