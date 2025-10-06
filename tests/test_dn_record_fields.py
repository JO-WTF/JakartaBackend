"""Test that DNRecord API endpoints return all fields."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import DN, DNRecord
from app.crud import ensure_dn, add_dn_record


client = TestClient(app)


@pytest.fixture
def db_session():
    """Create a test database session."""
    from app.db import SessionLocal, engine, Base
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # Clean up any existing test data
        db.query(DNRecord).filter(DNRecord.dn_number.like("TEST_DN_RECORD_%")).delete(synchronize_session=False)
        db.query(DN).filter(DN.dn_number.like("TEST_DN_RECORD_%")).delete(synchronize_session=False)
        db.commit()
        yield db
    finally:
        # Clean up after test
        db.rollback()
        db.query(DNRecord).filter(DNRecord.dn_number.like("TEST_DN_RECORD_%")).delete(synchronize_session=False)
        db.query(DN).filter(DN.dn_number.like("TEST_DN_RECORD_%")).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_get_dn_records_returns_all_fields(db_session: Session):
    """Test that GET /api/dn/{dn_number} returns all DNRecord fields including phone_number."""
    
    # Create a test DN
    ensure_dn(
        db_session,
        "TEST_DN_RECORD_001",
        lsp="Test LSP",
    )
    
    # Add a record with phone_number
    add_dn_record(
        db_session,
        dn_number="TEST_DN_RECORD_001",
        status="ARRIVED AT SITE",
        remark="Test remark",
        photo_url="https://example.com/photo.jpg",
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Test User",
        phone_number="081234567890",
    )
    
    # Call the API
    response = client.get("/api/dn/TEST_DN_RECORD_001")
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "items" in data
    assert len(data["items"]) == 1
    
    record = data["items"][0]
    
    # Verify all fields are present
    assert "id" in record
    assert "dn_number" in record
    assert "status" in record
    assert "remark" in record
    assert "photo_url" in record
    assert "lng" in record
    assert "lat" in record
    assert "updated_by" in record
    assert "phone_number" in record
    assert "created_at" in record
    
    # Verify field values
    assert record["dn_number"] == "TEST_DN_RECORD_001"
    assert record["status"] == "ARRIVED AT SITE"
    assert record["remark"] == "Test remark"
    assert record["photo_url"] == "https://example.com/photo.jpg"
    assert record["lng"] == "106.8456"
    assert record["lat"] == "-6.2088"
    assert record["updated_by"] == "Test User"
    assert record["phone_number"] == "081234567890"


def test_dn_search_returns_all_fields(db_session: Session):
    """Test that GET /api/dn/search returns all DNRecord fields."""
    
    # Create a test DN
    ensure_dn(
        db_session,
        "TEST_DN_RECORD_001",
        lsp="Test LSP",
    )
    
    # Add a record with phone_number
    add_dn_record(
        db_session,
        dn_number="TEST_DN_RECORD_001",
        status="ARRIVED AT SITE",
        remark="Test remark",
        photo_url="https://example.com/photo.jpg",
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Test User",
        phone_number="081234567890",
    )
    
    # Call the API
    response = client.get("/api/dn/search", params={"dn_number": "TEST_DN_RECORD_001"})
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "items" in data
    assert len(data["items"]) == 1
    
    record = data["items"][0]
    
    # Verify all fields are present
    assert record["dn_number"] == "TEST_DN_RECORD_001"
    assert record["phone_number"] == "081234567890"


def test_dn_search_filters_by_phone_number(db_session: Session):
    """Test that GET /api/dn/search filters results by phone_number."""

    ensure_dn(
        db_session,
        "TEST_DN_RECORD_001",
        lsp="Test LSP",
    )
    ensure_dn(
        db_session,
        "TEST_DN_RECORD_002",
        lsp="Test LSP",
    )

    add_dn_record(
        db_session,
        dn_number="TEST_DN_RECORD_001",
        status="ARRIVED",
        remark="Phone A",
        photo_url=None,
        lng=None,
        lat=None,
        du_id=None,
        updated_by="Tester",
        phone_number="081234567890",
    )
    add_dn_record(
        db_session,
        dn_number="TEST_DN_RECORD_002",
        status="ARRIVED",
        remark="Phone B",
        photo_url=None,
        lng=None,
        lat=None,
        du_id=None,
        updated_by="Tester",
        phone_number="089876543210",
    )

    response = client.get(
        "/api/dn/search",
        params={"phone_number": "089876543210"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["dn_number"] == "TEST_DN_RECORD_002"

    response_trimmed = client.get(
        "/api/dn/search",
        params={"phone_number": " 081234567890 "},
    )
    assert response_trimmed.status_code == 200
    data_trimmed = response_trimmed.json()
    assert data_trimmed["total"] == 1
    assert len(data_trimmed["items"]) == 1
    assert data_trimmed["items"][0]["dn_number"] == "TEST_DN_RECORD_001"


def test_dn_batch_returns_all_fields(db_session: Session):
    """Test that GET /api/dn/batch returns all DNRecord fields."""
    
    # Create a test DN
    ensure_dn(
        db_session,
        "TEST_DN_RECORD_001",
        lsp="Test LSP",
    )
    
    # Add a record with phone_number
    add_dn_record(
        db_session,
        dn_number="TEST_DN_RECORD_001",
        status="ARRIVED AT SITE",
        remark="Test remark",
        photo_url="https://example.com/photo.jpg",
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Test User",
        phone_number="081234567890",
    )
    
    # Call the API
    response = client.get("/api/dn/batch", params={"dn_number": "TEST_DN_RECORD_001"})
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "items" in data
    assert len(data["items"]) == 1
    
    record = data["items"][0]
    
    # Verify all fields are present
    assert record["dn_number"] == "TEST_DN_RECORD_001"
    assert record["phone_number"] == "081234567890"


def test_dn_records_with_null_values(db_session: Session):
    """Test that DNRecord fields work correctly with NULL values."""
    
    # Create a test DN
    ensure_dn(
        db_session,
        "TEST_DN_RECORD_001",
        lsp="Test LSP",
    )
    
    # Add a record WITHOUT du_id and phone_number
    add_dn_record(
        db_session,
        dn_number="TEST_DN_RECORD_001",
        status="ON THE WAY",
        remark="Test",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by=None,
        phone_number=None,
    )
    
    # Call the API
    response = client.get("/api/dn/TEST_DN_RECORD_001")
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    
    record = data["items"][0]
    
    # Verify NULL fields are present and None
    assert "phone_number" in record
    assert record["phone_number"] is None
    assert "updated_by" in record
    assert record["updated_by"] is None
