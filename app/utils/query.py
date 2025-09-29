"""Query parameter helpers."""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

from fastapi import HTTPException

from app.core.sync import DN_RE
from app.utils.string import normalize_dn

__all__ = ["collect_query_values", "normalize_batch_dn_numbers"]


def collect_query_values(*values: Any) -> list[str] | None:
    """Collect query parameter values supporting repeated parameters and comma-separated values."""
    normalized: list[str] = []
    seen: set[str] = set()

    def _add_candidate(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return
        parts = candidate.split(",") if "," in candidate else [candidate]
        for part in parts:
            trimmed = part.strip()
            if trimmed and trimmed not in seen:
                seen.add(trimmed)
                normalized.append(trimmed)

    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            _add_candidate(value)
            continue
        try:
            iterator: Iterable[Any] = iter(value)  # type: ignore[arg-type]
        except TypeError:
            continue
        for candidate in iterator:
            _add_candidate(candidate)

    return normalized or None


def normalize_batch_dn_numbers(*value_lists: Optional[List[str]]) -> list[str]:
    """Normalize DN numbers from multiple query values."""
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
        raise HTTPException(status_code=400, detail="Missing dn_number")

    invalid = [x for x in numbers if not DN_RE.fullmatch(x)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid DN number(s): {', '.join(invalid)}")

    return numbers
