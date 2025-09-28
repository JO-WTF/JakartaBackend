"""Shared helpers and constants for API endpoints."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterable, Optional

from app.time_utils import TZ_GMT7

DU_RE = re.compile(r"^.+$")
DN_RE = re.compile(r"^.+$")

VALID_STATUSES: tuple[str, ...] = (
    "PREPARE VEHICLE",
    "ON THE WAY",
    "ON SITE",
    "POD",
    "REPLAN MOS PROJECT",
    "WAITING PIC FEEDBACK",
    "REPLAN MOS DUE TO LSP DELAY",
    "CLOSE BY RN",
    "CANCEL MOS",
    "NO STATUS",
    "NEW MOS",
    "ARRIVED AT WH",
    "TRANSPORTING FROM WH",
    "ARRIVED AT XD/PM",
    "TRANSPORTING FROM XD/PM",
    "ARRIVED AT SITE",
    "开始运输",
    "运输中",
    "已到达",
    "过夜",
)

VALID_STATUS_DESCRIPTION = ", ".join(VALID_STATUSES)

VEHICLE_VALID_STATUSES: tuple[str, ...] = ("arrived", "departed")

_ZERO_WIDTH_CHARS = "\u200b\ufeff"
_ZERO_WIDTH_TRANS = {ord(ch): None for ch in _ZERO_WIDTH_CHARS}


@lru_cache(maxsize=4096)
def normalize_du(s: str) -> str:
    """Normalize DU identifiers using NFC and uppercase stripping rules."""

    if not s:
        return ""

    normalized = unicodedata.normalize("NFC", s)
    normalized = normalized.translate(_ZERO_WIDTH_TRANS)
    normalized = normalized.strip().upper()
    return normalized


def normalize_dn(value: str) -> str:
    """Normalize DN identifiers using the DU normalization rules."""

    return normalize_du(value)


def normalize_vehicle_plate(value: str) -> str:
    if not value:
        return ""

    return "".join(value.split()).upper()


def ensure_gmt7_timezone(dt: datetime | None) -> datetime | None:
    """Ensure datetime values are associated with the GMT+7 timezone."""

    if dt is None:
        return None

    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=TZ_GMT7)

    return dt


def _normalize_batch_dn_numbers(*value_lists: Optional[list[str]]) -> list[str]:
    raw_numbers: list[str] = []
    for values in value_lists:
        if not values:
            continue
        raw_numbers.extend(values)

    flat: list[str] = []
    for value in raw_numbers:
        if not value:
            continue
        for part in value.split(","):
            normalized = normalize_dn(part)
            if normalized:
                flat.append(normalized)

    numbers = [x for x in dict.fromkeys(flat) if x]

    if not numbers:
        raise ValueError("Missing dn_number")

    invalid = [x for x in numbers if not DN_RE.fullmatch(x)]
    if invalid:
        raise ValueError(f"Invalid DN number(s): {', '.join(invalid)}")

    return numbers


def _collect_query_values(*values: Any) -> list[str] | None:
    """Normalize query values supporting repeated or comma-separated entries."""

    normalized: list[str] = []
    seen: set[str] = set()

    def _add_candidate(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return

        parts: Iterable[str] = [candidate]
        if "," in candidate:
            parts = candidate.split(",")

        for part in parts:
            trimmed = part.strip()
            if not trimmed or trimmed in seen:
                continue
            seen.add(trimmed)
            normalized.append(trimmed)

    for value in values:
        if value is None:
            continue

        if isinstance(value, str):
            _add_candidate(value)
            continue

        try:
            iterator = iter(value)
        except TypeError:
            continue

        for candidate in iterator:
            _add_candidate(candidate)

    return normalized or None


__all__ = [
    "DU_RE",
    "DN_RE",
    "VALID_STATUSES",
    "VALID_STATUS_DESCRIPTION",
    "VEHICLE_VALID_STATUSES",
    "normalize_du",
    "normalize_dn",
    "normalize_vehicle_plate",
    "ensure_gmt7_timezone",
    "_normalize_batch_dn_numbers",
    "_collect_query_values",
]
