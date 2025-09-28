"""Shared Google Sheets helpers for interacting with gspread."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import gspread

from app.logging_utils import logger
from app.settings import settings

API_KEY = settings.google_api_key
DEFAULT_SPREADSHEET_URL = settings.google_spreadsheet_url
GS_KEY_PATH = Path("/etc/secrets/gskey.json")

try:
    gs_key_content = GS_KEY_PATH.read_text(encoding="utf-8")
    logger.info("Loaded gskey.json content: %s", gs_key_content)
except FileNotFoundError:
    logger.debug("gskey.json not found at %s, skipping", GS_KEY_PATH)
except Exception as exc:  # pragma: no cover - defensive logging
    logger.debug("Failed to read gskey.json from %s: %s", GS_KEY_PATH, exc)


def create_gspread_client() -> gspread.Client:
    """Return a gspread client using service-account credentials when possible."""

    try:
        logger.debug(
            "Attempting to create gspread client using service account file at %s",
            GS_KEY_PATH,
        )
        client = gspread.service_account(filename=str(GS_KEY_PATH))
        logger.info("Using gspread service account authentication")
        return client
    except Exception as exc:  # pragma: no cover - relies on environment setup
        logger.warning(
            "Failed to authenticate using service account at %s: %s. Falling back to API key.",
            GS_KEY_PATH,
            exc,
        )
        client = gspread.api_key(API_KEY)
        logger.info("Using gspread API key authentication")
        return client


def open_spreadsheet(
    client: gspread.Client | None = None,
    spreadsheet_url: str | None = None,
) -> gspread.Spreadsheet:
    """Open and return the spreadsheet specified by URL."""

    if client is None:
        client = create_gspread_client()
    if spreadsheet_url is None:
        spreadsheet_url = DEFAULT_SPREADSHEET_URL
    logger.debug("Opening spreadsheet URL: %s", spreadsheet_url)
    return client.open_by_url(spreadsheet_url)


def list_worksheets(spreadsheet: gspread.Spreadsheet) -> Iterable[gspread.Worksheet]:
    """Yield all worksheets available within the spreadsheet."""

    return spreadsheet.worksheets()


def get_worksheet(spreadsheet: gspread.Spreadsheet, title: str) -> gspread.Worksheet:
    """Return the worksheet matching the provided title."""

    return spreadsheet.worksheet(title)


def fetch_column_values(worksheet: gspread.Worksheet, column_index: int) -> list[str]:
    """Fetch all values from the specified column."""

    return worksheet.col_values(column_index)


def fetch_all_values(worksheet: gspread.Worksheet) -> list[list[str]]:
    """Fetch all rows for a worksheet."""

    return worksheet.get_all_values()


def fetch_cell(worksheet: gspread.Worksheet, row: int, column: int) -> gspread.cell.Cell:
    """Fetch a single cell from the worksheet."""

    return worksheet.cell(row, column)


def update_cell_value(
    worksheet: gspread.Worksheet,
    row: int,
    column: int,
    value: str,
) -> None:
    """Update a single cell value in the worksheet."""

    worksheet.update_cell(row, column, value)


__all__ = [
    "API_KEY",
    "DEFAULT_SPREADSHEET_URL",
    "GS_KEY_PATH",
    "create_gspread_client",
    "fetch_all_values",
    "fetch_cell",
    "fetch_column_values",
    "get_worksheet",
    "list_worksheets",
    "open_spreadsheet",
    "update_cell_value",
]
