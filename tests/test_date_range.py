from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.time_utils import parse_gmt7_date_range


def test_parse_gmt7_date_range_inclusive_end_of_day():
    date_from = datetime(2025, 9, 24, 16, 0, 0, tzinfo=timezone.utc)
    date_to = datetime(2025, 9, 26, 15, 59, 59, tzinfo=timezone.utc)

    start, end = parse_gmt7_date_range(date_from, date_to)

    assert start == datetime(2025, 9, 23, 17, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2025, 9, 26, 16, 59, 59, 999999, tzinfo=timezone.utc)


def test_parse_gmt7_date_range_handles_naive_inputs():
    date_from = datetime(2025, 9, 24, 16, 0, 0)
    date_to = datetime(2025, 9, 26, 15, 59, 59)

    start, end = parse_gmt7_date_range(date_from, date_to)

    assert start == datetime(2025, 9, 23, 17, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2025, 9, 26, 16, 59, 59, 999999, tzinfo=timezone.utc)


def test_parse_gmt7_date_range_allows_missing_bounds():
    date_from = datetime(2025, 9, 24, 16, 0, 0, tzinfo=timezone.utc)

    start, end = parse_gmt7_date_range(date_from, None)

    assert start == datetime(2025, 9, 23, 17, 0, 0, tzinfo=timezone.utc)
    assert end is None
