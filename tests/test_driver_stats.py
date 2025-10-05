"""Test driver statistics endpoint."""

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
        db.query(DNRecord).filter(DNRecord.dn_number.like("TEST_DRIVER_%")).delete(synchronize_session=False)
        db.query(DN).filter(DN.dn_number.like("TEST_DRIVER_%")).delete(synchronize_session=False)
        db.commit()
        yield db
    finally:
        # Clean up after test
        db.rollback()
        db.query(DNRecord).filter(DNRecord.dn_number.like("TEST_DRIVER_%")).delete(synchronize_session=False)
        db.query(DN).filter(DN.dn_number.like("TEST_DRIVER_%")).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_driver_stats_basic(db_session: Session):
    """Test basic driver statistics calculation."""
    
    # Create test DNs
    ensure_dn(db_session, "TEST_DRIVER_DN001", lsp="Test LSP")
    ensure_dn(db_session, "TEST_DRIVER_DN002", lsp="Test LSP")
    ensure_dn(db_session, "TEST_DRIVER_DN003", lsp="Test LSP")
    
    # Driver 1: 2 unique DNs, 3 unique (DN, status) combinations
    add_dn_record(
        db_session,
        dn_number="TEST_DRIVER_DN001",
        status="ARRIVED AT SITE",
        remark="Test 1",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Driver 1",
        phone_number="081234567890",
    )
    add_dn_record(
        db_session,
        dn_number="TEST_DRIVER_DN001",
        status="COMPLETED",
        remark="Test 2",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Driver 1",
        phone_number="081234567890",
    )
    add_dn_record(
        db_session,
        dn_number="TEST_DRIVER_DN002",
        status="ARRIVED AT SITE",
        remark="Test 3",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Driver 1",
        phone_number="081234567890",
    )
    
    # Driver 2: 1 unique DN, 1 unique (DN, status) combination
    add_dn_record(
        db_session,
        dn_number="TEST_DRIVER_DN003",
        status="ON THE WAY",
        remark="Test 4",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Driver 2",
        phone_number="089876543210",
    )
    
    # Call the API
    response = client.get("/api/dn/status-delivery/by-driver")
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "data" in data
    assert "total_drivers" in data
    
    # Find our test drivers in the response
    driver1_stats = None
    driver2_stats = None
    
    for driver in data["data"]:
        if driver["phone_number"] == "081234567890":
            driver1_stats = driver
        elif driver["phone_number"] == "089876543210":
            driver2_stats = driver
    
    # Verify Driver 1 stats
    assert driver1_stats is not None
    assert driver1_stats["unique_dn_count"] == 2
    assert driver1_stats["record_count"] == 3
    
    # Verify Driver 2 stats
    assert driver2_stats is not None
    assert driver2_stats["unique_dn_count"] == 1
    assert driver2_stats["record_count"] == 1


def test_driver_stats_duplicate_status(db_session: Session):
    """Test that duplicate status records for the same DN are counted only once."""
    
    # Create test DN
    ensure_dn(db_session, "TEST_DRIVER_DN004", lsp="Test LSP")
    
    # Add multiple records with the same status (should only count once)
    for i in range(3):
        add_dn_record(
            db_session,
            dn_number="TEST_DRIVER_DN004",
            status="ARRIVED AT SITE",
            remark=f"Duplicate {i}",
            photo_url=None,
            lng="106.8456",
            lat="-6.2088",
            du_id=None,
            updated_by="Driver 3",
            phone_number="085555555555",
        )
    
    # Add a record with different status
    add_dn_record(
        db_session,
        dn_number="TEST_DRIVER_DN004",
        status="COMPLETED",
        remark="Different status",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Driver 3",
        phone_number="085555555555",
    )
    
    # Call the API
    response = client.get("/api/dn/status-delivery/by-driver")
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    
    # Find Driver 3
    driver3_stats = None
    for driver in data["data"]:
        if driver["phone_number"] == "085555555555":
            driver3_stats = driver
            break
    
    # Should have 1 unique DN and 2 unique (DN, status) combinations
    assert driver3_stats is not None
    assert driver3_stats["unique_dn_count"] == 1
    assert driver3_stats["record_count"] == 2  # Only 2 unique (DN, status) pairs


def test_driver_stats_filter_by_phone(db_session: Session):
    """Test filtering by specific phone number."""
    
    # Create test DNs
    ensure_dn(db_session, "TEST_DRIVER_DN005", lsp="Test LSP")
    ensure_dn(db_session, "TEST_DRIVER_DN006", lsp="Test LSP")
    
    # Add records for two drivers
    add_dn_record(
        db_session,
        dn_number="TEST_DRIVER_DN005",
        status="ARRIVED AT SITE",
        remark="Driver 4",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Driver 4",
        phone_number="081111111111",
    )
    
    add_dn_record(
        db_session,
        dn_number="TEST_DRIVER_DN006",
        status="ARRIVED AT SITE",
        remark="Driver 5",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Driver 5",
        phone_number="082222222222",
    )
    
    # Call the API with phone filter
    response = client.get("/api/dn/status-delivery/by-driver", params={"phone_number": "081111111111"})
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    
    # Should only return Driver 4
    assert len(data["data"]) >= 1
    
    # Check if Driver 4 is in results
    driver4_found = False
    driver5_found = False
    
    for driver in data["data"]:
        if driver["phone_number"] == "081111111111":
            driver4_found = True
            assert driver["unique_dn_count"] == 1
        if driver["phone_number"] == "082222222222":
            driver5_found = True
    
    assert driver4_found is True
    assert driver5_found is False


def test_driver_stats_excludes_null_phone(db_session: Session):
    """Test that records with null or empty phone numbers are excluded."""
    
    # Create test DN
    ensure_dn(db_session, "TEST_DRIVER_DN007", lsp="Test LSP")
    
    # Add records with null/empty phone numbers
    add_dn_record(
        db_session,
        dn_number="TEST_DRIVER_DN007",
        status="ARRIVED AT SITE",
        remark="No phone",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Driver X",
        phone_number=None,
    )
    
    add_dn_record(
        db_session,
        dn_number="TEST_DRIVER_DN007",
        status="COMPLETED",
        remark="Empty phone",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Driver Y",
        phone_number="",
    )
    
    # Call the API
    response = client.get("/api/dn/status-delivery/by-driver")
    
    # Verify response
    assert response.status_code == 200
    data = response.json()
    
    # Verify that no driver with null or empty phone number is in results
    for driver in data["data"]:
        assert driver["phone_number"] is not None
        assert driver["phone_number"] != ""
