"""Test DN update_count functionality."""

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_DISK_PATH", "./data/uploads")

from app.models import Base, DN, DNRecord  # noqa: E402
from app.crud import add_dn_record, ensure_dn  # noqa: E402


@pytest.fixture
def test_db():
    """Create a test database session."""
    # Use in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_update_count_initialization(test_db):
    """Test that new DN has update_count initialized to 0."""
    dn_number = "DN001"
    
    # Create a DN
    ensure_dn(test_db, dn_number, status="NO STATUS")
    
    # Check that update_count is 0
    dn = test_db.query(DN).filter(DN.dn_number == dn_number).first()
    assert dn is not None
    assert dn.update_count == 0


def test_update_count_increments(test_db):
    """Test that update_count increments when adding DN records."""
    dn_number = "DN002"
    
    # Create a DN first
    ensure_dn(test_db, dn_number, status="NO STATUS")
    
    # Add first record
    add_dn_record(
        test_db,
        dn_number=dn_number,
        status="ON THE WAY",
        remark="First update",
        photo_url=None,
        lng=None,
        lat=None,
    )
    
    # Check update_count is 1
    dn = test_db.query(DN).filter(DN.dn_number == dn_number).first()
    assert dn.update_count == 1
    
    # Add second record
    add_dn_record(
        test_db,
        dn_number=dn_number,
        status="ON SITE",
        remark="Second update",
        photo_url=None,
        lng=None,
        lat=None,
    )
    
    # Check update_count is 2
    test_db.refresh(dn)
    assert dn.update_count == 2
    
    # Add third record
    add_dn_record(
        test_db,
        dn_number=dn_number,
        status="POD",
        remark="Third update",
        photo_url=None,
        lng=None,
        lat=None,
    )
    
    # Check update_count is 3
    test_db.refresh(dn)
    assert dn.update_count == 3


def test_update_count_matches_record_count(test_db):
    """Test that update_count matches the number of DN records."""
    dn_number = "DN003"
    
    # Create a DN first
    ensure_dn(test_db, dn_number, status="NO STATUS")
    
    # Add multiple records
    for i in range(5):
        add_dn_record(
            test_db,
            dn_number=dn_number,
            status=f"STATUS_{i}",
            remark=f"Update {i+1}",
            photo_url=None,
            lng=None,
            lat=None,
        )
    
    # Check update_count
    dn = test_db.query(DN).filter(DN.dn_number == dn_number).first()
    assert dn.update_count == 5
    
    # Verify against actual record count
    record_count = test_db.query(DNRecord).filter(DNRecord.dn_number == dn_number).count()
    assert dn.update_count == record_count
