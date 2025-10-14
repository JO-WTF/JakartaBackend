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
]

# Regular expression for DN number validation
DN_RE = re.compile(r"^.+$")

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
STATUS_DELIVERY_LOOKUP.update({
    "Arrive at Warehouse": "ARRIVED AT WH",
    "TRANSPORTING FROM WH": "DEPARTED FROM WH",
    "TRANSPORTING FROM XD/PM": "DEPARTED FROM XD/PM",
})

# Statuses that trigger arrival timestamp (write to column S)
ARRIVAL_STATUSES: frozenset[str] = frozenset({
    "ARRIVED AT XD/PM",
    "ARRIVED AT SITE",
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
