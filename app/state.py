"""Runtime state utilities for in-memory shared variables.

This module maintains a thread-safe mapping of Google Sheet title -> sheet id
that is updated whenever Google Sheets are synchronised or accessed.
"""
from __future__ import annotations

import threading
from typing import Any

__all__ = [
    "get_gs_sheet_name_to_id_map",
    "set_gs_sheet_name_to_id_map",
    "update_gs_map_from_sheets",
    "get_sheet_id_by_name",
    "clear_gs_sheet_name_to_id_map",
]

# internal lock + map
_lock = threading.RLock()
_gs_sheet_name_to_id_map: dict[str, int] = {}


def get_gs_sheet_name_to_id_map() -> dict[str, int]:
    """Return a shallow copy of the current sheet name -> id mapping."""
    with _lock:
        return dict(_gs_sheet_name_to_id_map)


def set_gs_sheet_name_to_id_map(new_map: dict[str, int]) -> None:
    """Replace the current mapping with new_map (thread-safe)."""
    with _lock:
        _gs_sheet_name_to_id_map.clear()
        _gs_sheet_name_to_id_map.update(new_map or {})


def update_gs_map_from_sheets(sheets: list[Any]) -> None:
    """Update the internal mapping from a list of gspread Worksheet-like objects.

    Each worksheet is expected to have attributes `.title` and `.id`.
    """
    if not sheets:
        # Clear mapping if no sheets provided
        set_gs_sheet_name_to_id_map({})
        return
    mapping: dict[str, int] = {}
    for sheet in sheets:
        try:
            title = getattr(sheet, "title")
            sid = getattr(sheet, "id", None)
            if title is not None and sid is not None:
                mapping[str(title)] = int(sid)
        except Exception:
            # be defensive: skip any object that doesn't match expected shape
            continue
    set_gs_sheet_name_to_id_map(mapping)


def get_sheet_id_by_name(name: str) -> int | None:
    """Get sheet id by its title, or None if unknown."""
    if name is None:
        return None
    with _lock:
        return _gs_sheet_name_to_id_map.get(name)


def clear_gs_sheet_name_to_id_map() -> None:
    """Clear the in-memory mapping."""
    set_gs_sheet_name_to_id_map({})
