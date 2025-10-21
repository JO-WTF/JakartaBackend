"""Time-related helpers for Jakarta backend."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

PLAN_MOS_DATE_FORMATS: tuple[str, ...] = (
    "%d %b %y",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%Y/%m/%d",
)

__all__ = [
    "TZ_GMT7",
    "ensure_gmt7_timezone",
    "to_gmt7_iso",
    "parse_gmt7_date_range",
    "parse_plan_mos_date",
]


TZ_GMT7 = timezone(timedelta(hours=7))


def ensure_gmt7_timezone(dt: datetime | None) -> datetime | None:
    """Attach GMT+7 timezone to naive datetimes."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ_GMT7)
    return dt


def to_gmt7_iso(dt: datetime | None) -> str | None:
    """Convert a datetime to an ISO8601 string in GMT+7."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_GMT7).isoformat()


def parse_gmt7_date_range(
    date_from: datetime | None, date_to: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """Normalize incoming datetimes to GMT+7 day boundaries."""

    def _normalize(value: datetime | None, is_start: bool) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        local_value = value.astimezone(TZ_GMT7)
        boundary_time = time(0, 0, 0) if is_start else time(23, 59, 59, 999_999)
        localized = datetime.combine(local_value.date(), boundary_time, tzinfo=TZ_GMT7)
        return localized.astimezone(timezone.utc)

    return _normalize(date_from, True), _normalize(date_to, False)


def parse_plan_mos_date(value: str | None) -> date | None:
    """Parse a Plan MOS date string into a ``date`` if possible."""

    if not value or not isinstance(value, str):
        return None

    trimmed = value.strip()
    if not trimmed:
        return None

    normalized = trimmed
    month_replacements = {
        "Sept": "Sep",
        "SEPT": "Sep",
        "sept": "Sep",
        "Okt": "Oct",
        "OKT": "Oct",
        "okt": "Oct",
    }
    for incorrect, correct in month_replacements.items():
        normalized = normalized.replace(incorrect, correct)

    for fmt in PLAN_MOS_DATE_FORMATS:
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue

    return None
