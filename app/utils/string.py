"""String normalization helpers."""

from __future__ import annotations

from functools import lru_cache
import unicodedata

__all__ = ["normalize_dn", "normalize_vehicle_plate"]

_ZERO_WIDTH_CHARS = "\u200b\ufeff"
_ZERO_WIDTH_TRANS = {ord(ch): None for ch in _ZERO_WIDTH_CHARS}


@lru_cache(maxsize=4096)
def normalize_dn(value: str) -> str:
    """Normalize DN numbers using NFC form and uppercase."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.translate(_ZERO_WIDTH_TRANS)
    return normalized.strip().upper()


def normalize_vehicle_plate(value: str) -> str:
    if not value:
        return ""
    return "".join(value.split()).upper()
