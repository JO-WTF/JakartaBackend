"""Test that driver_contact_number is protected from Google Sheet updates when update_count > 0."""

import pytest
from sqlalchemy.orm import Session
from app.crud import ensure_dn, add_dn_record
from app.core.sync import sync_dn_sheet_to_db
from unittest.mock import patch, MagicMock
import pandas as pd


@pytest.fixture
def db_session():
    """Create a test database session."""
    from app.db import SessionLocal, engine, Base
    from app.models import DN, DNRecord
    
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # Clean up any existing test data
        db.query(DNRecord).filter(DNRecord.dn_number.in_(["DN001", "DN002", "DN003"])).delete(synchronize_session=False)
        db.query(DN).filter(DN.dn_number.in_(["DN001", "DN002", "DN003"])).delete(synchronize_session=False)
        db.commit()
        yield db
    finally:
        # Clean up after test
        db.rollback()
        db.query(DNRecord).filter(DNRecord.dn_number.in_(["DN001", "DN002", "DN003"])).delete(synchronize_session=False)
        db.query(DN).filter(DN.dn_number.in_(["DN001", "DN002", "DN003"])).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_driver_contact_number_protection_with_update_count(db_session: Session):
    """Test that driver_contact_number is NOT updated from Google Sheet when update_count > 0."""
    
    # 1. Create a DN with initial phone number
    dn = ensure_dn(
        db_session,
        "DN001",
        driver_contact_number="081234567890",
        lsp="LSP A",
        status_wh="Status WH A",
    )
    assert dn.driver_contact_number == "081234567890"
    assert dn.update_count == 0
    
    # 2. Add a record to increment update_count
    add_dn_record(
        db_session,
        dn_number="DN001",
        status="ARRIVED AT SITE",
        remark="Test arrival",
        photo_url=None,
        lng="106.8456",
        lat="-6.2088",
        du_id=None,
        updated_by="Test User",
        phone_number="081234567890",
    )
    
    # 3. Verify update_count was incremented
    db_session.refresh(dn)
    assert dn.update_count == 1
    
    # 4. Mock Google Sheet data with DIFFERENT phone number
    mock_sheet_data = pd.DataFrame([{
        "dn_number": "DN001",
        "driver_contact_number": "089999999999",  # Different number
        "lsp": "LSP A",
        "status_wh": "Status WH A",
        "plan_mos_date": "01 Jan 25",
    }])
    
    # 5. Mock the sheet fetching functions
    with patch("app.core.sync.create_gspread_client") as mock_client, \
         patch("app.core.sync.process_all_sheets") as mock_process:
        
        mock_gc = MagicMock()
        mock_sh = MagicMock()
        mock_client.return_value = mock_gc
        mock_gc.open_by_url.return_value = mock_sh
        mock_process.return_value = mock_sheet_data
        
        # 6. Run sync
        sync_dn_sheet_to_db(db_session)
    
    # 7. Verify the phone number was NOT changed (protected)
    db_session.refresh(dn)
    assert dn.driver_contact_number == "081234567890", \
        "Phone number should NOT be updated from Google Sheet when update_count > 0"
    assert dn.update_count == 1, "Update count should remain unchanged"


def test_driver_contact_number_updates_when_update_count_zero(db_session: Session):
    """Test that driver_contact_number CAN be updated from Google Sheet when update_count = 0."""
    
    # 1. Create a DN with initial phone number and update_count = 0
    dn = ensure_dn(
        db_session,
        "DN002",
        driver_contact_number="081234567890",
        lsp="LSP B",
        status_wh="Status WH B",
    )
    assert dn.driver_contact_number == "081234567890"
    assert dn.update_count == 0
    
    # 2. Mock Google Sheet data with DIFFERENT phone number
    mock_sheet_data = pd.DataFrame([{
        "dn_number": "DN002",
        "driver_contact_number": "089999999999",  # Different number
        "lsp": "LSP B",
        "status_wh": "Status WH B",
        "plan_mos_date": "01 Jan 25",
    }])
    
    # 3. Mock the sheet fetching functions
    with patch("app.core.sync.create_gspread_client") as mock_client, \
         patch("app.core.sync.process_all_sheets") as mock_process:
        
        mock_gc = MagicMock()
        mock_sh = MagicMock()
        mock_client.return_value = mock_gc
        mock_gc.open_by_url.return_value = mock_sh
        mock_process.return_value = mock_sheet_data
        
        # 4. Run sync
        sync_dn_sheet_to_db(db_session)
    
    # 5. Verify the phone number WAS changed (allowed)
    db_session.refresh(dn)
    assert dn.driver_contact_number == "089999999999", \
        "Phone number SHOULD be updated from Google Sheet when update_count = 0"
    assert dn.update_count == 0, "Update count should remain 0"


def test_driver_contact_number_multiple_updates(db_session: Session):
    """Test that driver_contact_number is protected after multiple updates."""
    
    # 1. Create a DN with initial phone number
    dn = ensure_dn(
        db_session,
        "DN003",
        driver_contact_number="081234567890",
        lsp="LSP C",
        status_wh="Status WH C",
    )
    assert dn.driver_contact_number == "081234567890"
    assert dn.update_count == 0
    
    # 2. Add multiple records to increment update_count
    for i in range(3):
        add_dn_record(
            db_session,
            dn_number="DN003",
            status=f"Update {i+1}",
            remark=f"Test update {i+1}",
            photo_url=None,
            lng="106.8456",
            lat="-6.2088",
            du_id=None,
            updated_by="Test User",
            phone_number="081234567890",
        )
    
    # 3. Verify update_count is now 3
    db_session.refresh(dn)
    assert dn.update_count == 3
    
    # 4. Mock Google Sheet data with DIFFERENT phone number
    mock_sheet_data = pd.DataFrame([{
        "dn_number": "DN003",
        "driver_contact_number": "089999999999",  # Different number
        "lsp": "LSP C",
        "status_wh": "Status WH C",
        "plan_mos_date": "01 Jan 25",
    }])
    
    # 5. Mock the sheet fetching functions
    with patch("app.core.sync.create_gspread_client") as mock_client, \
         patch("app.core.sync.process_all_sheets") as mock_process:
        
        mock_gc = MagicMock()
        mock_sh = MagicMock()
        mock_client.return_value = mock_gc
        mock_gc.open_by_url.return_value = mock_sh
        mock_process.return_value = mock_sheet_data
        
        # 6. Run sync
        sync_dn_sheet_to_db(db_session)
    
    # 7. Verify the phone number was NOT changed (still protected)
    db_session.refresh(dn)
    assert dn.driver_contact_number == "081234567890", \
        "Phone number should NOT be updated from Google Sheet when update_count = 3"
    assert dn.update_count == 3, "Update count should remain 3"
