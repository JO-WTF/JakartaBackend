"""Sync utilities for the Aging Orders Google Sheet."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Tuple
from uuid import uuid4
from urllib.parse import unquote_plus

from gspread.utils import rowcol_to_a1
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.google import AGING_ORDERS_SPREADSHEET_URL, create_gspread_client
from app.models import AgingOrder
from app.utils.logging import logger
from app.db import SessionLocal

__all__ = [
    "sync_aging_orders_sheet_to_db",
    "scheduled_aging_orders_sheet_sync",
    "update_pm_location_by_order_name",
    "update_pm_location_in_sheets",
    "run_pm_location_sheet_updates",
]

_EXCLUDED_SHEETS = {"pm location & contact pic", "other"}

# Map normalized sheet header -> model field
_FIELD_MAP: Dict[str, str] = {
    "shipment no": "shipment_no",
    "order name": "order_name",
    "shipment status": "shipment_status",
    "source location": "source_location",
    "destination location": "destination_location",
    "service provider": "service_provider",
    "insert time": "insert_time",
    "ata": "ata",
    "global pod cycle statistic": "global_pod_cycle_statistic",
    "period": "period",
    "pm location": "pm_location",
    "last status": "last_status",
    "remark": "remark",
}


@dataclass
class AgingOrderUpdateResult:
    updated_count: int
    created: bool
    shipment_no: str | None = None
    sheet_title: str | None = None
    sheet_row: int | None = None
    sheet_cell: str | None = None
    sheet_updates_scheduled: bool = False
    created_row_pending_sheet: bool = False


def _normalize_header(value: Any) -> str:
    text = str(value or "").lower()
    for ch in (".", "_"):
        text = text.replace(ch, " ")
    return " ".join(text.split())


def _normalize_headers(headers: List[Any]) -> List[str]:
    return [_normalize_header(header) for header in headers]


def _normalize_cell(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    normalized_keys = {_normalize_header(key): val for key, val in raw.items()}
    payload: Dict[str, Any] = {}
    for source_key, target_key in _FIELD_MAP.items():
        payload[target_key] = _normalize_cell(normalized_keys.get(source_key))
    return payload


def _normalize_text_input(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _find_order_positions(spreadsheet: Any, target_order: str) -> List[Tuple[Any, int, int]]:
    """Locate all matches of order_name across worksheets, returning [(worksheet, row_index, pm_col)]."""
    positions: List[Tuple[Any, int, int]] = []
    for sheet in spreadsheet.worksheets():
        if _normalize_header(sheet.title) in _EXCLUDED_SHEETS:
            continue
        try:
            values = sheet.get_all_values()
        except Exception as exc:  # pragma: no cover - gspread runtime errors
            logger.warning("Failed to read worksheet %s when searching order %s: %s", sheet.title, target_order, exc)
            continue
        if not values:
            continue
        headers = _normalize_headers(values[0])
        try:
            order_col_idx = headers.index("order name")
        except ValueError:
            continue
        try:
            pm_col_idx = headers.index("pm location")
        except ValueError:
            pm_col_idx = None

        if pm_col_idx is None:
            logger.warning("Worksheet %s missing PM Location column while searching order %s", sheet.title, target_order)
            continue

        for row_index, row_values in enumerate(values[1:], start=2):
            if len(row_values) <= order_col_idx:
                continue
            if _normalize_text_input(row_values[order_col_idx]) != target_order:
                continue
            positions.append((sheet, row_index, pm_col_idx + 1))
    return positions


def _find_unknown_worksheet(spreadsheet: Any) -> Any | None:
    """Return the worksheet whose title normalizes to 'unknown'."""
    for sheet in spreadsheet.worksheets():
        if _normalize_header(sheet.title) == "unknown":
            return sheet
    return None


def _generate_unknown_shipment_no(order_name: str) -> str:
    """Generate a unique shipment_no for Unknown sheet rows."""
    sanitized = "".join(ch if ch.isalnum() else "-" for ch in order_name) or "order"
    sanitized = "-".join(part for part in sanitized.split("-") if part)  # collapse repeated hyphens
    prefix = sanitized[:32]
    return f"UNKNOWN-{prefix}-{uuid4().hex[:6]}"


def _append_order_to_unknown_sheet(order_name: str, pm_value: str, shipment_no: str) -> Tuple[str, int, str] | None:
    """Append a new row to the Unknown worksheet. Returns (sheet_title, row_index, sheet_cell) or None."""
    if not AGING_ORDERS_SPREADSHEET_URL:
        logger.warning("Skipping Unknown sheet append: missing AGING_ORDERS_SPREADSHEET_URL")
        return None

    try:
        client = create_gspread_client()
        spreadsheet = client.open_by_url(AGING_ORDERS_SPREADSHEET_URL)
        worksheet = _find_unknown_worksheet(spreadsheet)
        if worksheet is None:
            logger.warning("Unknown worksheet not found in Aging Orders spreadsheet")
            return None

        headers = worksheet.row_values(1)
        normalized_headers = _normalize_headers(headers)
        header_index = {header: idx for idx, header in enumerate(normalized_headers)}

        row_values = [""] * len(headers)

        def set_value(header_key: str, value: str | None) -> None:
            idx = header_index.get(header_key)
            if idx is not None and value is not None:
                row_values[idx] = value

        set_value("shipment no", shipment_no)
        set_value("order name", order_name)
        set_value("pm location", pm_value)

        # Determine the row index that will be used for appending
        current_rows = len(worksheet.get_all_values())
        target_row_index = max(current_rows + 1, 2)

        worksheet.append_row(row_values, value_input_option="USER_ENTERED")

        shipment_col_idx = header_index.get("shipment no", 0)
        sheet_cell = rowcol_to_a1(target_row_index, shipment_col_idx + 1)

        return worksheet.title, target_row_index, sheet_cell
    except Exception as exc:  # pragma: no cover - gspread runtime errors
        logger.warning("Failed to append aging order to Unknown sheet: %s", exc)
        return None


def run_pm_location_sheet_updates(
    *,
    order_name: str,
    pm_value: str,
    created: bool,
    shipment_no: str | None,
) -> None:
    """Background-friendly wrapper to update/append Aging Orders PM location in Google Sheets."""
    try:
        with SessionLocal() as db:
            decoded_pm_value = unquote_plus(pm_value) if isinstance(pm_value, str) else pm_value
            normalized_order = _normalize_text_input(order_name)
            normalized_pm = _normalize_text_input(decoded_pm_value)

            if not normalized_order or not normalized_pm:
                return

            if created and shipment_no:
                row = (
                    db.query(AgingOrder)
                    .filter(AgingOrder.shipment_no == shipment_no)
                    .one_or_none()
                )
                if row is None:
                    # Ensure the Unknown-sheet entry also exists in DB when created via the API
                    row = AgingOrder(
                        shipment_no=shipment_no,
                        order_name=normalized_order,
                        pm_location=normalized_pm,
                        is_deleted=False,
                    )
                    db.add(row)
                    db.commit()

                sheet_info = _append_order_to_unknown_sheet(normalized_order, normalized_pm, shipment_no)
                if sheet_info:
                    sheet_title, sheet_row, sheet_cell = sheet_info
                    row.sheet_title = sheet_title
                    row.sheet_row = sheet_row
                    row.sheet_cell = sheet_cell
                    db.add(row)
                    db.commit()
                return

            rows = (
                db.query(AgingOrder)
                .filter(func.lower(func.trim(AgingOrder.order_name)) == normalized_order.lower())
                .filter(AgingOrder.is_deleted.is_(False))
                .all()
            )
            if not rows:
                return
            update_pm_location_in_sheets(rows, normalized_pm, order_name=normalized_order)
    except Exception:  # pragma: no cover
        logger.exception("Background PM Location sheet update failed", extra={"order_name": order_name})


def update_pm_location_in_sheets(rows: List[AgingOrder], pm_value: str, *, order_name: str | None = None) -> None:
    """Best-effort update of PM Location for the given sheet rows.

    If the stored row's order_name does not match the target order_name, we search
    all worksheets for the order_name and update the PM Location there.
    """
    if not rows:
        return
    if not AGING_ORDERS_SPREADSHEET_URL:
        logger.warning("Skipping sheet update: missing AGING_ORDERS_SPREADSHEET_URL")
        return

    normalized_target_order = _normalize_text_input(order_name) if order_name else None
    visited_positions: set[Tuple[str, int, int]] = set()
    fallback_needed = False

    try:
        client = create_gspread_client()
        spreadsheet = client.open_by_url(AGING_ORDERS_SPREADSHEET_URL)
        sheet_cache: Dict[str, Any] = {}
        pm_col_cache: Dict[str, int] = {}
        order_col_cache: Dict[str, int] = {}

        for row in rows:
            if not row.sheet_title or not row.sheet_row:
                continue

            worksheet = None
            target_row_index = row.sheet_row
            pm_col = None
            order_col = None

            row_order = _normalize_text_input(getattr(row, "order_name", None))
            worksheet = sheet_cache.get(row.sheet_title)
            if worksheet is None:
                try:
                    worksheet = spreadsheet.worksheet(row.sheet_title)
                    sheet_cache[row.sheet_title] = worksheet
                except Exception as exc:  # pragma: no cover - gspread runtime errors
                    logger.warning("Failed to open worksheet %s: %s", row.sheet_title, exc)
                    continue

            pm_col = pm_col_cache.get(row.sheet_title)
            if pm_col is None:
                try:
                    headers = worksheet.row_values(1)
                    normalized_headers = _normalize_headers(headers)
                    pm_col = normalized_headers.index("pm location") + 1
                    pm_col_cache[row.sheet_title] = pm_col
                    order_col = normalized_headers.index("order name") + 1 if "order name" in normalized_headers else None
                    if order_col:
                        order_col_cache[row.sheet_title] = order_col
                except ValueError:
                    logger.warning("Worksheet %s missing PM Location column", row.sheet_title)
                    continue
                except Exception as exc:  # pragma: no cover
                    logger.warning("Failed to read headers for worksheet %s: %s", row.sheet_title, exc)
                    continue
            order_col = order_col_cache.get(row.sheet_title, order_col)

            if normalized_target_order and order_col is None:
                try:
                    headers = worksheet.row_values(1)
                    normalized_headers = _normalize_headers(headers)
                    order_col = normalized_headers.index("order name") + 1
                    order_col_cache[row.sheet_title] = order_col
                except ValueError:
                    logger.warning("Worksheet %s missing Order Name column", row.sheet_title)
                    fallback_needed = True
                    continue
                except Exception as exc:  # pragma: no cover
                    logger.warning("Failed to read headers for worksheet %s: %s", row.sheet_title, exc)
                    fallback_needed = True
                    continue

            if normalized_target_order and order_col:
                try:
                    sheet_order_val = worksheet.cell(target_row_index, order_col).value
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "Failed to read order_name at sheet %s row %s col %s: %s",
                        row.sheet_title,
                        target_row_index,
                        order_col,
                        exc,
                    )
                    sheet_order_val = None
                normalized_sheet_order = _normalize_text_input(sheet_order_val)
                if normalized_sheet_order != normalized_target_order:
                    fallback_needed = True
                    continue

            pos_key = (worksheet.title, target_row_index, pm_col)
            if pos_key in visited_positions:
                continue
            visited_positions.add(pos_key)

            try:
                a1_cell = rowcol_to_a1(target_row_index, pm_col)
                worksheet.update_acell(a1_cell, pm_value)
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "Failed to update PM Location in sheet %s at row %s col %s: %s",
                    worksheet.title if worksheet else row.sheet_title,
                    target_row_index,
                    pm_col,
                    exc,
                )
                continue

        # Fallback: if any rows were mismatched, search all sheets for the target order and update all matches
        if fallback_needed and normalized_target_order:
            fallback_positions = _find_order_positions(spreadsheet, normalized_target_order)
            for worksheet, row_idx, pm_col in fallback_positions:
                pos_key = (worksheet.title, row_idx, pm_col)
                if pos_key in visited_positions:
                    continue
                try:
                    a1_cell = rowcol_to_a1(row_idx, pm_col)
                    worksheet.update_acell(a1_cell, pm_value)
                    visited_positions.add(pos_key)
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "Failed fallback PM Location update in sheet %s at row %s col %s: %s",
                        worksheet.title,
                        row_idx,
                        pm_col,
                        exc,
                    )
                    continue
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to update PM Location in Google Sheets: %s", exc)


def sync_aging_orders_sheet_to_db(db: Session) -> dict[str, int]:
    """Fetch Aging Orders sheets and upsert rows into the database."""
    if not AGING_ORDERS_SPREADSHEET_URL:
        raise RuntimeError("Missing AGING_ORDERS_SPREADSHEET_URL environment variable")

    logger.info("Starting Aging Orders sheet sync")

    client = create_gspread_client()
    spreadsheet = client.open_by_url(AGING_ORDERS_SPREADSHEET_URL)

    worksheets = [
        sheet for sheet in spreadsheet.worksheets() if _normalize_header(sheet.title) not in _EXCLUDED_SHEETS
    ]

    collected: List[Dict[str, Any]] = []
    for sheet in worksheets:
        try:
            values = sheet.get_all_values()
        except Exception as exc:  # pragma: no cover - gspread runtime errors
            logger.exception("Failed to read worksheet %s: %s", sheet.title, exc)
            continue

        if not values:
            continue
        headers = _normalize_headers(values[0])
        try:
            shipment_col_idx = headers.index("shipment no")
        except ValueError:
            shipment_col_idx = 0

        for row_index, row_values in enumerate(values[1:], start=2):
            if not any((cell or "").strip() for cell in row_values):
                continue
            row_dict = {headers[idx]: row_values[idx] for idx in range(min(len(headers), len(row_values)))}
            normalized = _normalize_row(row_dict)
            if not any(normalized.values()):
                continue
            shipment_no = normalized.get("shipment_no")
            if not shipment_no:
                continue
            normalized["is_deleted"] = False
            normalized["sheet_title"] = sheet.title
            normalized["sheet_row"] = row_index
            normalized["sheet_cell"] = rowcol_to_a1(row_index, shipment_col_idx + 1)
            collected.append(normalized)

    if not collected:
        logger.info("No aging orders rows found to sync")
        return {"created": 0, "updated": 0, "total": 0}

    deduped: Dict[str, Dict[str, Any]] = {}
    for row in collected:
        deduped[row["shipment_no"]] = row

    incoming_rows = list(deduped.values())
    incoming_keys = set(deduped.keys())

    existing_rows = db.query(AgingOrder.shipment_no, AgingOrder.is_deleted).all()
    existing_shipments = {value for (value, _) in existing_rows}
    created_count = len(incoming_keys - existing_shipments)
    updated_count = len(incoming_keys & existing_shipments)

    stmt = insert(AgingOrder).values(incoming_rows)
    update_columns: Dict[str, Any] = {}
    for column in AgingOrder.__table__.columns:
        if column.name in ("id", "created_at"):
            continue
        if column.name == "updated_at":
            update_columns[column.name] = func.now()
        elif column.name == "is_deleted":
            # Any incoming row should be marked as not deleted
            update_columns[column.name] = stmt.excluded[column.name]
        else:
            update_columns[column.name] = stmt.excluded[column.name]

    stmt = stmt.on_conflict_do_update(index_elements=[AgingOrder.shipment_no], set_=update_columns)
    db.execute(stmt)

    to_soft_delete = [value for (value, is_deleted) in existing_rows if value not in incoming_keys and not is_deleted]
    soft_deleted = 0
    if to_soft_delete:
        db.query(AgingOrder).filter(AgingOrder.shipment_no.in_(to_soft_delete)).update(
            {"is_deleted": True, "updated_at": func.now()},
            synchronize_session=False,
        )
        soft_deleted = len(to_soft_delete)

    db.commit()

    logger.info(
        "Synced %d aging orders rows (created=%d, updated=%d)",
        len(incoming_rows),
        created_count,
        updated_count,
    )

    return {"created": created_count, "updated": updated_count, "soft_deleted": soft_deleted, "total": len(incoming_rows)}


def update_pm_location_by_order_name(
    db: Session,
    *,
    order_name: str,
    pm_name: str,
    skip_sheet_updates: bool = False,
) -> AgingOrderUpdateResult:
    """Update PM Location using order_name lookup; create in Unknown sheet if not found.

    Args:
        order_name: The order name to match (case-insensitive, trimmed).
        pm_name: The PM name to set.

    Returns:
        AgingOrderUpdateResult with update/create metadata.
    """
    normalized_order = _normalize_text_input(order_name)
    decoded_pm_name = unquote_plus(pm_name) if isinstance(pm_name, str) else pm_name
    normalized_pm = _normalize_text_input(decoded_pm_name)

    if not normalized_order:
        raise ValueError("order_name is required")
    if not normalized_pm:
        raise ValueError("pm_name is required")

    matched_rows = (
        db.query(AgingOrder)
        .filter(func.lower(func.trim(AgingOrder.order_name)) == normalized_order.lower())
        .filter(AgingOrder.is_deleted.is_(False))
        .all()
    )
    if not matched_rows:
        # Create a new row and append to Unknown sheet
        shipment_no = _generate_unknown_shipment_no(normalized_order)
        new_row = AgingOrder(
            shipment_no=shipment_no,
            order_name=normalized_order,
            pm_location=normalized_pm,
            is_deleted=False,
        )

        sheet_title = None
        sheet_row = None
        sheet_cell = None

        if not skip_sheet_updates:
            sheet_info = _append_order_to_unknown_sheet(normalized_order, normalized_pm, shipment_no)
            if sheet_info:
                sheet_title, sheet_row, sheet_cell = sheet_info
                new_row.sheet_title = sheet_title
                new_row.sheet_row = sheet_row
                new_row.sheet_cell = sheet_cell

        db.add(new_row)
        db.commit()

        logger.info(
            "Created new aging order row for order_name=%s in Unknown sheet (shipment_no=%s)",
            normalized_order,
            shipment_no,
        )
        return AgingOrderUpdateResult(
            updated_count=0,
            created=True,
            shipment_no=shipment_no,
            sheet_title=sheet_title,
            sheet_row=sheet_row,
            sheet_cell=sheet_cell,
            sheet_updates_scheduled=skip_sheet_updates,
            created_row_pending_sheet=skip_sheet_updates,
        )

    # Update database rows
    now_ts = datetime.utcnow()
    for row in matched_rows:
        row.pm_location = normalized_pm
        row.updated_at = now_ts
    db.commit()

    # Best-effort update to Google Sheets at the stored row/column position
    if not skip_sheet_updates:
        update_pm_location_in_sheets(matched_rows, normalized_pm, order_name=normalized_order)

    updated_count = len(matched_rows)
    logger.info("Updated pm_location for %d aging order rows (order_name=%s)", updated_count, normalized_order)
    return AgingOrderUpdateResult(
        updated_count=updated_count,
        created=False,
        shipment_no=matched_rows[0].shipment_no if matched_rows else None,
        sheet_title=matched_rows[0].sheet_title if matched_rows else None,
        sheet_row=matched_rows[0].sheet_row if matched_rows else None,
        sheet_cell=matched_rows[0].sheet_cell if matched_rows else None,
        sheet_updates_scheduled=skip_sheet_updates,
        created_row_pending_sheet=False,
    )


def sync_aging_orders_sheet_with_new_session() -> dict[str, int]:
    """Sync Aging Orders sheet using a fresh DB session."""
    db = SessionLocal()
    try:
        return sync_aging_orders_sheet_to_db(db)
    finally:
        db.close()


async def scheduled_aging_orders_sheet_sync() -> None:
    """Background-friendly Aging Orders sync runner for the scheduler."""
    try:
        stats = await asyncio.to_thread(sync_aging_orders_sheet_with_new_session)
    except RuntimeError as exc:
        logger.warning("Scheduled Aging Orders sheet sync skipped: %s", exc)
    except Exception:
        logger.exception("Scheduled Aging Orders sheet sync failed")
    else:
        logger.info(
            "Scheduled Aging Orders sheet sync completed (created=%d, updated=%d, soft_deleted=%d, total=%d)",
            stats.get("created", 0),
            stats.get("updated", 0),
            stats.get("soft_deleted", 0),
            stats.get("total", 0),
        )
