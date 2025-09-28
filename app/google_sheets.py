"""Shared Google Sheets helpers for interacting with gspread."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable

import gspread

from app.logging_utils import logger
from app.settings import settings

DEFAULT_SPREADSHEET_URL = settings.google_spreadsheet_url


def _load_service_account_credentials() -> Dict[str, Any]:
    """Load and parse the service account credentials from settings."""

    raw_credentials = settings.google_service_account_credentials
    try:
        credentials = json.loads(raw_credentials)
    except json.JSONDecodeError as exc:  # pragma: no cover - validated during startup
        logger.error("Invalid Google service account credentials JSON: %s", exc)
        raise

    logger.debug("Loaded Google service account credentials from configuration")
    return credentials


def create_gspread_client() -> gspread.Client:
    """Return a gspread client using service-account credentials."""

    credentials = _load_service_account_credentials()
    logger.info("Using gspread service account authentication from configuration")
    return gspread.service_account_from_dict(credentials)


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
    "DEFAULT_SPREADSHEET_URL",
    "_load_service_account_credentials",
    "create_gspread_client",
    "fetch_all_values",
    "fetch_cell",
    "fetch_column_values",
    "get_worksheet",
    "list_worksheets",
    "open_spreadsheet",
    "update_cell_value",
]
