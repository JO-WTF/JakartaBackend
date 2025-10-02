"""Test timestamp update functionality for DN status changes."""

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

# Set required environment variables before importing app modules
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test?sslmode=require")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_CREDENTIALS", "{}")
os.environ.setdefault("STORAGE_DISK_PATH", "/tmp/test_uploads")

from app.core.sheet import sync_status_timestamp_to_sheet  # noqa: E402
from app.utils.time import TZ_GMT7  # noqa: E402


class TestTimestampUpdate:
    """Test cases for timestamp update based on status."""

    def test_timestamp_format(self):
        """Test that timestamp is formatted correctly without leading zeros."""
        # Test with specific datetime
        test_time = datetime(2025, 10, 2, 7, 10, 0, tzinfo=TZ_GMT7)
        formatted = f"{test_time.month}/{test_time.day}/{test_time.year} {test_time.hour}:{test_time.minute:02d}:{test_time.second:02d}"
        
        # Should be "10/2/2025 7:10:00" not "10/02/2025 07:10:00"
        assert formatted == "10/2/2025 7:10:00"
        
        # Test another case with double-digit hour
        test_time2 = datetime(2025, 1, 5, 14, 5, 3, tzinfo=TZ_GMT7)
        formatted2 = f"{test_time2.month}/{test_time2.day}/{test_time2.year} {test_time2.hour}:{test_time2.minute:02d}:{test_time2.second:02d}"
        
        assert formatted2 == "1/5/2025 14:05:03"

    @patch('app.core.sheet.create_gspread_client')
    @patch('app.core.sheet.datetime')
    def test_arrived_at_site_writes_to_column_s(self, mock_datetime, mock_gspread):
        """Test that ARRIVED AT SITE status writes to column S (actual_arrive_time_ata)."""
        # Mock datetime.now to return specific time
        mock_time = datetime(2025, 10, 2, 7, 10, 0, tzinfo=TZ_GMT7)
        mock_datetime.now.return_value = mock_time
        
        # Mock gspread objects
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_worksheet = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = "TEST001"
        
        mock_gspread.return_value = mock_client
        mock_client.open_by_url.return_value = mock_spreadsheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_worksheet.cell.return_value = mock_cell
        
        # Call function with ARRIVED AT SITE status
        result = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST001",
            status="ARRIVED AT SITE"
        )
        
        # Verify it tried to update column S (position 19)
        assert result is not None
        assert result.get("column_name") == "actual_arrive_time_ata"
        # Column position should be 19 (S is 19th column)
        assert result.get("column") == 19
        assert result.get("status") == "ARRIVED AT SITE"

    @patch('app.core.sheet.create_gspread_client')
    @patch('app.core.sheet.datetime')
    def test_arrived_at_xd_pm_writes_to_column_s(self, mock_datetime, mock_gspread):
        """Test that ARRIVED AT XD/PM status writes to column S (actual_arrive_time_ata)."""
        # Mock datetime.now to return specific time
        mock_time = datetime(2025, 10, 2, 7, 10, 0, tzinfo=TZ_GMT7)
        mock_datetime.now.return_value = mock_time
        
        # Mock gspread objects
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_worksheet = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = "TEST001B"
        
        mock_gspread.return_value = mock_client
        mock_client.open_by_url.return_value = mock_spreadsheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_worksheet.cell.return_value = mock_cell
        
        # Call function with ARRIVED AT XD/PM status
        result = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST001B",
            status="ARRIVED AT XD/PM"
        )
        
        # Verify it tried to update column S (position 19)
        assert result is not None
        assert result.get("column_name") == "actual_arrive_time_ata"
        assert result.get("column") == 19
        assert result.get("status") == "ARRIVED AT XD/PM"

    @patch('app.core.sheet.create_gspread_client')
    @patch('app.core.sheet.datetime')
    def test_transporting_from_wh_writes_to_column_r(self, mock_datetime, mock_gspread):
        """Test that TRANSPORTING FROM WH status writes to column R (actual_depart_from_start_point_atd)."""
        # Mock datetime.now to return specific time
        mock_time = datetime(2025, 10, 2, 7, 10, 0, tzinfo=TZ_GMT7)
        mock_datetime.now.return_value = mock_time
        
        # Mock gspread objects
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_worksheet = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = "TEST002"
        
        mock_gspread.return_value = mock_client
        mock_client.open_by_url.return_value = mock_spreadsheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_worksheet.cell.return_value = mock_cell
        
        # Call function with TRANSPORTING FROM WH status
        result = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST002",
            status="TRANSPORTING FROM WH"
        )
        
        # Verify it tried to update column R (position 18)
        assert result is not None
        assert result.get("column_name") == "actual_depart_from_start_point_atd"
        # Column position should be 18 (R is 18th column)
        assert result.get("column") == 18
        assert result.get("status") == "TRANSPORTING FROM WH"

    @patch('app.core.sheet.create_gspread_client')
    @patch('app.core.sheet.datetime')
    def test_transporting_from_xd_pm_writes_to_column_r(self, mock_datetime, mock_gspread):
        """Test that TRANSPORTING FROM XD/PM status writes to column R (actual_depart_from_start_point_atd)."""
        # Mock datetime.now to return specific time
        mock_time = datetime(2025, 10, 2, 7, 10, 0, tzinfo=TZ_GMT7)
        mock_datetime.now.return_value = mock_time
        
        # Mock gspread objects
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_worksheet = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = "TEST002B"
        
        mock_gspread.return_value = mock_client
        mock_client.open_by_url.return_value = mock_spreadsheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_worksheet.cell.return_value = mock_cell
        
        # Call function with TRANSPORTING FROM XD/PM status
        result = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST002B",
            status="TRANSPORTING FROM XD/PM"
        )
        
        # Verify it tried to update column R (position 18)
        assert result is not None
        assert result.get("column_name") == "actual_depart_from_start_point_atd"
        assert result.get("column") == 18
        assert result.get("status") == "TRANSPORTING FROM XD/PM"

    @patch('app.core.sheet.create_gspread_client')
    def test_other_status_returns_none(self, mock_gspread):
        """Test that statuses not in the defined groups return None (no timestamp update)."""
        # No need to set up mocks since function should return early
        
        # Test with various statuses that should not trigger timestamp update
        result1 = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST003",
            status="IN TRANSIT"
        )
        assert result1 is None
        
        result2 = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST003",
            status="ON THE WAY"
        )
        assert result2 is None
        
        result3 = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST003",
            status="POD"
        )
        assert result3 is None

    @patch('app.core.sheet.create_gspread_client')
    @patch('app.core.sheet.datetime')
    def test_case_insensitive_status_check(self, mock_datetime, mock_gspread):
        """Test that status check is case-insensitive."""
        # Mock datetime.now
        mock_time = datetime(2025, 10, 2, 7, 10, 0, tzinfo=TZ_GMT7)
        mock_datetime.now.return_value = mock_time
        
        # Mock gspread objects
        mock_client = MagicMock()
        mock_spreadsheet = MagicMock()
        mock_worksheet = MagicMock()
        mock_cell = MagicMock()
        mock_cell.value = "TEST004"
        
        mock_gspread.return_value = mock_client
        mock_client.open_by_url.return_value = mock_spreadsheet
        mock_spreadsheet.worksheet.return_value = mock_worksheet
        mock_worksheet.cell.return_value = mock_cell
        
        # Test with lowercase
        result1 = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST004",
            status="arrived at site"
        )
        assert result1.get("column_name") == "actual_arrive_time_ata"
        
        # Test with mixed case
        result2 = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST004",
            status="Arrived At Site"
        )
        assert result2.get("column_name") == "actual_arrive_time_ata"
        
        # Test with extra spaces
        result3 = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST004",
            status="  ARRIVED AT SITE  "
        )
        assert result3.get("column_name") == "actual_arrive_time_ata"
        
        # Test departure status case-insensitive
        result4 = sync_status_timestamp_to_sheet(
            sheet_name="Plan MOS Test",
            row_index=5,
            dn_number="TEST004",
            status="transporting from wh"
        )
        assert result4.get("column_name") == "actual_depart_from_start_point_atd"
