import json
import os
from datetime import datetime, timezone

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


def test_list_check_results_filters_by_created_date(db_session: Session, client):
    first = CheckResult(
        report_id="report-list-1",
        dn_number="TESTDN12345678",
        lsp="LSP01",
        checker_name="Alice",
        check_time="2026-07-28 10:00:00",
        status="completed",
        box_count=2,
        checked_count=2,
        boxes_json=json.dumps([{"boxNo": "BOX1", "status": "Checked"}]),
        created_at=datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc),
    )
    second = CheckResult(
        report_id="report-list-2",
        dn_number="TESTDN87654321",
        checker_name="Bob",
        status="completed",
        box_count=1,
        checked_count=1,
        created_at=datetime(2026, 7, 29, 4, 0, tzinfo=timezone.utc),
    )
    db_session.add_all([first, second])
    db_session.commit()

    response = client.get("/api/dn/check_result", params={"date": "2026-07-28"})

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["total"] == 1
    assert data["items"][0]["report_id"] == "report-list-1"
    assert data["items"][0]["dn_number"] == "TESTDN12345678"


def test_list_check_results_filters_by_partial_dn_number(db_session: Session, client):
    first = CheckResult(
        report_id="report-partial-1",
        dn_number="TESTDN12345678",
        status="completed",
        created_at=datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc),
    )
    second = CheckResult(
        report_id="report-partial-2",
        dn_number="OTHERDN87654321",
        status="completed",
        created_at=datetime(2026, 7, 28, 5, 0, tzinfo=timezone.utc),
    )
    db_session.add_all([first, second])
    db_session.commit()

    response = client.get(
        "/api/dn/check_result",
        params={"date": "2026-07-28", "dn_number": "123456"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["dn_number"] == "TESTDN12345678"


def test_get_check_result_returns_detail_payload(db_session: Session, client):
    record = CheckResult(
        report_id="report-detail-1",
        dn_number="TESTDN12345678",
        checker_name="Alice",
        status="completed",
        box_count=1,
        checked_count=1,
        boxes_json=json.dumps([{"boxNo": "BOX1", "itemNo": "ITEM1", "status": "Checked"}]),
        metadata_json=json.dumps({"generatedAt": "2026-07-28T10:00:00Z"}),
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)

    response = client.get(f"/api/dn/check_result/{record.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["item"]["report_id"] == "report-detail-1"
    assert data["item"]["boxes"][0]["boxNo"] == "BOX1"
    assert data["item"]["metadata"]["generatedAt"] == "2026-07-28T10:00:00Z"
