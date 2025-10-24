"""Global constants used across the application."""

from __future__ import annotations

import re

__all__ = [
    "DN_RE",
    "VALID_STATUSES",
    "VALID_STATUS_DESCRIPTION",
    "VEHICLE_VALID_STATUSES",
    "STANDARD_STATUS_DELIVERY_VALUES",
    "STATUS_DELIVERY_LOOKUP",
    "ARRIVAL_STATUSES",
    "DEPARTURE_STATUSES",
    "EARLY_BIRD_AREA_THRESHOLDS",
]

# Regular expression for DN number validation
DN_RE = re.compile(r"^[A-Za-z]{2,5}\d{11,16}$")

# Valid DN statuses
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
    "DEPARTED FROM WH",
    "DEPARTED FROM XD/PM",
    "TRANSPORTING FROM WH",
    "ARRIVED AT XD/PM",
    "TRANSPORTING FROM XD/PM",
    "ARRIVED AT SITE",
    "开始运输",
    "运输中",
    "已到达",
    "过夜",
)

# Human-readable description of valid statuses
VALID_STATUS_DESCRIPTION = ", ".join(VALID_STATUSES)

# Valid vehicle statuses
VEHICLE_VALID_STATUSES: tuple[str, ...] = ("arrived", "departed")

# Standard status_delivery values for normalization
STANDARD_STATUS_DELIVERY_VALUES: tuple[str, ...] = (
    "ARRIVED AT WH",
    "DEPARTED FROM WH",
    "ARRIVED AT XD/PM",
    "DEPARTED FROM XD/PM",
    "ARRIVED AT SITE",
    "POD",
)

# Lookup table for normalizing status_delivery values
STATUS_DELIVERY_LOOKUP: dict[str, str] = {
    canonical.lower(): canonical for canonical in STANDARD_STATUS_DELIVERY_VALUES
}
# Additional synonyms for status_delivery. Keys are lower-cased to match lookup usage
STATUS_DELIVERY_LOOKUP.update(
    {k.lower(): v for k, v in {
        "Arrive at Warehouse": "ARRIVED AT WH",
        "TRANSPORTING FROM WH": "DEPARTED FROM WH",
        "TRANSPORTING FROM XD/PM": "DEPARTED FROM XD/PM",
    }.items()}
)

# Statuses that trigger arrival timestamp (write to column S)
ARRIVAL_STATUSES: frozenset[str] = frozenset({
    "ARRIVED AT XD/PM",
    "ARRIVED AT SITE",
    "POD"
})

# Statuses that trigger departure timestamp (write to column R)
DEPARTURE_STATUSES: frozenset[str] = frozenset(
    {
        "TRANSPORTING FROM WH",
        "TRANSPORTING FROM XD/PM",
        "DEPARTED FROM WH",
        "DEPARTED FROM XD/PM",
    }
)

# Area-specific arrival thresholds (hour in GMT+7) used by the early-bird report.
EARLY_BIRD_AREA_THRESHOLDS: dict[str, int] = {
    "jabo": 6,
    "new": 6,
    "wj": 6,
    "swap": 6,
    "5g": 6,
    "exp": 6,
    "west java": 6,
    "zone3": 6,
    "zone2": 6,
    "zone1": 6,
    "zone 3": 6,
    "zone 2": 6,
    "zone 1": 6,
    "central kalimantan": 7,
    "west kalimantan": 7,
    "south sumatera": 7,
    "central sumatera": 7,
    "central north sumatera": 7,
    "bali": 5,
    "ntb": 6,
    "central java": 6,
    "east java": 6,
    "ntt": 6,
}
