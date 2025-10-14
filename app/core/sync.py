"""DN Sheet synchronisation core logic."""

from __future__ import annotations

import asyncio
import json
import traceback
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any, List

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

# Re-export constants for backward compatibility
from app.constants import (
    DN_RE,
    STANDARD_STATUS_DELIVERY_VALUES,
    STATUS_DELIVERY_LOOKUP,
    VALID_STATUSES,
    VALID_STATUS_DESCRIPTION,
    VEHICLE_VALID_STATUSES,
)
from app.core.google import SPREADSHEET_URL, create_gspread_client
from app.core.sheet import (
    process_all_sheets,
    normalize_sheet_value,
    parse_date,
)
from app.crud import create_dn_sync_log, get_dn_map_by_numbers, get_latest_dn_records_map, _ACTIVE_DN_EXPR
from app.db import SessionLocal
from app.dn_columns import get_mutable_dn_columns
from app.models import DN, Vehicle
from app.utils.logging import dn_sync_logger, logger
from app.utils.string import normalize_dn
from app.utils.time import to_gmt7_iso

__all__ = [
    # Re-exported constants for backward compatibility
    "DN_RE",
    "VALID_STATUSES",
    "VALID_STATUS_DESCRIPTION",
    "VEHICLE_VALID_STATUSES",
    "STANDARD_STATUS_DELIVERY_VALUES",
    # Module-specific exports
    "DnSyncResult",
    "sync_dn_sheet_to_db",
    "sync_dn_sheet_with_new_session",
    "run_dn_sheet_sync_once",
    "scheduled_dn_sheet_sync",
    "normalize_database_fields",
    "serialize_vehicle",
    "_normalize_status_delivery_value",
]


@dataclass
class DnSyncResult:
    """Aggregated DN sync outcome."""

    synced_numbers: List[str]
    created_count: int
    updated_count: int
    ignored_count: int


def _values_match(existing_value: Any, new_value: Any) -> bool:
    if existing_value is None and new_value is None:
        return True
    if isinstance(existing_value, str):
        existing_value = existing_value.strip() or None
    if isinstance(new_value, str):
        new_value = new_value.strip() or None
    return existing_value == new_value


def _normalize_status_delivery_value(raw_value: str | None) -> str | None:
    """Normalize delivery status input to standard values.

    Args:
        raw_value: The raw status delivery value to normalize

    Returns:
        Normalized status delivery value or None if empty/invalid
        For non-string values, returns the value as-is
        For standard values (case-insensitive), returns the canonical format
        For non-standard values, returns the trimmed value with normalized whitespace
    """
    # Handle None and non-string types
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        return raw_value

    # Trim and normalize whitespace
    trimmed = " ".join(raw_value.split())
    if not trimmed:
        return None

    # Check if it's a standard value (case-insensitive)
    normalized = STATUS_DELIVERY_LOOKUP.get(trimmed.lower())
    if normalized:
        return normalized

    # For non-standard values, return the normalized whitespace version
    return trimmed


def normalize_database_fields(db: Session) -> None:
    """Normalize plan_mos_date and status_delivery fields in database."""
    dn_sync_logger.debug("Starting database field normalization")

    dn_entries = db.query(DN).filter(DN.plan_mos_date.isnot(None)).filter(_ACTIVE_DN_EXPR).all()
    normalized_plan_dates = 0

    for entry in dn_entries:
        raw_value = entry.plan_mos_date.strip() if entry.plan_mos_date else None
        if not raw_value:
            continue
        parsed_value = parse_date(raw_value)
        if isinstance(parsed_value, datetime):
            normalized_value = parsed_value.strftime("%d %b %y")
            if normalized_value != entry.plan_mos_date:
                entry.plan_mos_date = normalized_value
                normalized_plan_dates += 1

    status_entries = db.query(DN).filter(_ACTIVE_DN_EXPR).all()
    normalized_status_delivery = 0
    for entry in status_entries:
        normalized_value = _normalize_status_delivery_value(entry.status_delivery)
        if normalized_value is None:
            normalized_value = "No Status"
        if normalized_value != entry.status_delivery:
            entry.status_delivery = normalized_value
            normalized_status_delivery += 1

    if normalized_plan_dates or normalized_status_delivery:
        db.commit()

    if normalized_plan_dates:
        dn_sync_logger.info("Normalized plan_mos_date for %d DN rows", normalized_plan_dates)
    else:
        dn_sync_logger.debug("No plan_mos_date values required normalization")
    if normalized_status_delivery:
        dn_sync_logger.info("Normalized status_delivery for %d DN rows", normalized_status_delivery)
    else:
        dn_sync_logger.debug("No status_delivery values required normalization")


def sync_dn_sheet_to_db(db: Session) -> DnSyncResult:
    """Synchronise Google Sheet data into the database."""
    start_time = datetime.utcnow()
    dn_sync_logger.info("Starting sync_dn_sheet_to_db run")

    try:
        client_start = perf_counter()
        gc = create_gspread_client()
        dn_sync_logger.debug("Created gspread client in %.3fs", perf_counter() - client_start)
        open_start = perf_counter()
        sh = gc.open_by_url(SPREADSHEET_URL)
        dn_sync_logger.debug("Spreadsheet opened in %.3fs", perf_counter() - open_start)
        sheet_start = perf_counter()
        combined_df = process_all_sheets(sh)
        dn_sync_logger.debug("Fetched+combined sheet data in %.3fs", perf_counter() - sheet_start)

        # Deduplicate by dn_number, keeping the last occurrence
        if not combined_df.empty and "dn_number" in combined_df.columns:
            original_rows = len(combined_df)
            combined_df = combined_df.drop_duplicates(subset=["dn_number"], keep="last")
            deduplicated_rows = len(combined_df)
            if original_rows != deduplicated_rows:
                logger.info(
                    "Deduplicated Google Sheets data: %d rows -> %d rows (removed %d duplicates)",
                    original_rows,
                    deduplicated_rows,
                    original_rows - deduplicated_rows,
                )
    except Exception as exc:
        logger.exception("Failed to fetch DN sheet data: %s", exc)
        dn_sync_logger.exception("Failed to fetch DN sheet data")
        raise

    sheet_columns: List[str] = list(combined_df.columns)
    records: List[dict[str, Any]] = []
    dn_numbers: set[str] = set()

    total_rows = len(combined_df) if not combined_df.empty else 0
    skipped_missing_number = 0
    skipped_empty_payload = 0
    dn_sync_logger.info("Preparing to process %d sheet rows", total_rows)

    processing_start = perf_counter()

    row_normalization_total = 0.0
    plan_mos_parse_total = 0.0
    plan_mos_parse_count = 0
    dn_normalization_total = 0.0
    record_build_total = 0.0
    rows_iterated = 0
    rows_persisted = 0

    if not combined_df.empty:
        columns_tuple = tuple(sheet_columns)
        try:
            dn_index = sheet_columns.index("dn_number")
        except ValueError:
            dn_sync_logger.warning("Sheet data missing 'dn_number' column; skipping processing")
            dn_index = None

        plan_mos_index = sheet_columns.index("plan_mos_date") if "plan_mos_date" in sheet_columns else None
        status_delivery_index = sheet_columns.index("status_delivery") if "status_delivery" in sheet_columns else None

        # Track duplicate DN numbers for logging
        dn_occurrence_count: dict[str, int] = {}

        if dn_index is not None:
            for row_values in combined_df.itertuples(index=False, name=None):
                rows_iterated += 1
                row_normalization_start = perf_counter()
                normalized_row: list[Any] = []
                has_payload = False
                original_plan_mos_date = None  # Track original plan_mos_date for logging

                for idx, raw_value in enumerate(row_values):
                    normalized_value = normalize_sheet_value(raw_value)
                    if (
                        plan_mos_index is not None
                        and idx == plan_mos_index
                        and isinstance(normalized_value, str)
                        and normalized_value
                    ):
                        parse_start = perf_counter()
                        original_plan_mos_date = normalized_value  # Store original value for logging
                        parsed_plan_mos_date = parse_date(normalized_value)
                        plan_mos_parse_total += perf_counter() - parse_start
                        plan_mos_parse_count += 1
                        if isinstance(parsed_plan_mos_date, datetime):
                            normalized_value = parsed_plan_mos_date.strftime("%d %b %y")

                    if idx != dn_index and normalized_value is not None:
                        has_payload = True

                    normalized_row.append(normalized_value)

                row_normalization_total += perf_counter() - row_normalization_start

                if status_delivery_index is not None:
                    normalized_status = _normalize_status_delivery_value(normalized_row[status_delivery_index])
                    normalized_row[status_delivery_index] = normalized_status

                dn_normalization_start = perf_counter()
                raw_number = normalized_row[dn_index]
                raw_number_str = str(raw_number).strip() if raw_number is not None else ""
                normalized_number = normalize_dn(raw_number_str) if raw_number_str else ""
                dn_normalization_total += perf_counter() - dn_normalization_start
                if not normalized_number:
                    skipped_missing_number += 1
                    continue

                if not has_payload:
                    skipped_empty_payload += 1
                    continue

                record_build_start = perf_counter()
                normalized_row[dn_index] = normalized_number
                cleaned = dict(zip(columns_tuple, normalized_row))

                # Track duplicate DN numbers
                dn_occurrence_count[normalized_number] = dn_occurrence_count.get(normalized_number, 0) + 1
                if dn_occurrence_count[normalized_number] > 1:
                    logger.warning(
                        "Duplicate DN %s found (occurrence #%d) - later rows will overwrite earlier ones",
                        normalized_number,
                        dn_occurrence_count[normalized_number],
                    )

                # Log plan_mos_date processing for debugging
                if plan_mos_index is not None and original_plan_mos_date is not None:
                    current_plan_mos_date = cleaned.get("plan_mos_date")
                    logger.debug(
                        "DN %s plan_mos_date processing: original='%s' -> normalized='%s'",
                        normalized_number,
                        original_plan_mos_date,
                        current_plan_mos_date,
                    )

                records.append(cleaned)
                record_build_total += perf_counter() - record_build_start
                rows_persisted += 1
                dn_numbers.add(normalized_number)

        # Log duplicate DN statistics
        duplicate_dns = {dn: count for dn, count in dn_occurrence_count.items() if count > 1}
        if duplicate_dns:
            logger.warning(
                "Found %d duplicate DN numbers in Google Sheets: %s",
                len(duplicate_dns),
                dict(list(duplicate_dns.items())[:5]),  # Show first 5 duplicates
            )
    else:
        dn_sync_logger.info("Combined DataFrame is empty; no rows to process")

    if not dn_numbers:
        dn_sync_logger.info(
            "No DN numbers extracted (skipped_missing=%d, skipped_empty=%d)",
            skipped_missing_number,
            skipped_empty_payload,
        )
        return DnSyncResult(synced_numbers=[], created_count=0, updated_count=0, ignored_count=0)

    latest_records_for_update = get_latest_dn_records_map(db, dn_numbers)
    existing_dn_map = get_dn_map_by_numbers(db, dn_numbers)
    mutable_columns = set(get_mutable_dn_columns())

    create_payload_by_number: dict[str, dict[str, Any]] = {}
    update_payload_by_number: dict[str, dict[str, Any]] = {}
    numbers_to_create: set[str] = set()
    numbers_to_update: set[str] = set()
    numbers_unchanged: set[str] = set()

    assignable_filter_total = 0.0
    change_detection_total = 0.0
    payload_mutation_total = 0.0
    latest_merge_total = 0.0
    created_columns: set[str] = set()
    updated_columns: set[str] = set()
    created_field_total = 0
    updated_field_total = 0

    for entry in records:
        number = entry["dn_number"]
        sheet_fields = {key: entry.get(key) for key in sheet_columns if key != "dn_number"}
        latest = latest_records_for_update.get(number)
        existing_dn = existing_dn_map.get(number)
        if latest:
            merge_start = perf_counter()
            # date format: 05 Oct 25
            entry_plan_mos_date = (
                datetime.strptime(entry.get("plan_mos_date"), "%d %b %y") if entry.get("plan_mos_date") else None
            )
            existing_plan_mos_date = datetime.strptime(existing_dn.plan_mos_date, "%d %b %y") if existing_dn.plan_mos_date else None
            logger.info("Merging DN %s: entry_plan_mos_date='%s', existing_plan_mos_date='%s'",
                        number, entry_plan_mos_date, existing_plan_mos_date)

            # Update sheet_fields: use chosen status and other values from latest
            sheet_fields.update(
                {
                    "status_delivery": _normalize_status_delivery_value(entry.get("status_delivery")),
                    "status_site": entry.get("status_site"),
                    "remark": entry.get("remark"),
                    "photo_url": latest.photo_url,
                    "lng": latest.lng,
                    "lat": latest.lat,
                }
            )
            latest_merge_total += perf_counter() - merge_start
        elif not existing_dn and number not in numbers_to_create:
            dn_sync_logger.debug("Preparing creation for DN %s from sheet data", number)

        assignable_start = perf_counter()
        assignable_fields = {k: v for k, v in sheet_fields.items() if k in mutable_columns}
        assignable_filter_total += perf_counter() - assignable_start

        comparison_start = perf_counter()
        if existing_dn:
            changed_fields: dict[str, Any] = {}
            for key, value in assignable_fields.items():
                # Protect driver_contact_number from being overwritten if DN has been updated
                if key == "driver_contact_number" and (existing_dn.update_count or 0) > 0:
                    # Skip this field - don't allow Google Sheet to overwrite it
                    dn_sync_logger.debug(
                        "Skipping driver_contact_number update for DN %s (update_count=%d > 0)",
                        number,
                        existing_dn.update_count,
                    )
                    continue
                if not _values_match(getattr(existing_dn, key, None), value):
                    changed_fields[key] = value
            change_detection_total += perf_counter() - comparison_start
            if not changed_fields:
                numbers_unchanged.add(number)
                continue
            if number not in numbers_to_update:
                dn_sync_logger.debug("Preparing update for existing DN %s after detecting differences", number)
            numbers_to_update.add(number)
            updated_columns.update(changed_fields.keys())
            payload = update_payload_by_number.setdefault(number, {"id": existing_dn.id, "dn_number": number})
            mutation_start = perf_counter()
            payload.update(changed_fields)
            payload_mutation_total += perf_counter() - mutation_start
            updated_field_total += len(changed_fields)
        else:
            change_detection_total += perf_counter() - comparison_start
            numbers_to_create.add(number)
            created_columns.update(assignable_fields.keys())
            payload = create_payload_by_number.setdefault(number, {"dn_number": number})
            mutation_start = perf_counter()
            payload.update(assignable_fields)
            payload_mutation_total += perf_counter() - mutation_start
            created_field_total += len(assignable_fields)

    processing_duration = perf_counter() - processing_start
    total_payloads = len(create_payload_by_number) + len(update_payload_by_number)
    dn_sync_logger.debug(
        "Prepared %d DN payloads (create=%d, update=%d) in %.3fs",
        total_payloads,
        len(numbers_to_create),
        len(numbers_to_update),
        processing_duration,
    )
    unchanged_count = len(numbers_unchanged)
    dn_sync_logger.info(
        (
            "DN payload summary: create=%d (fields=%d, columns=%d), "
            "update=%d (fields=%d, columns=%d), unchanged=%d (processing_time=%.3fs)"
        ),
        len(numbers_to_create),
        created_field_total,
        len(created_columns),
        len(numbers_to_update),
        updated_field_total,
        len(updated_columns),
        unchanged_count,
        processing_duration,
    )

    create_payloads = list(create_payload_by_number.values())
    update_payloads = list(update_payload_by_number.values())
    created_count = len(create_payloads)
    updated_count = len(update_payloads)

    if create_payloads or update_payloads:
        db_start = perf_counter()
        if create_payloads:
            insert_stmt = insert(DN).on_conflict_do_nothing(index_elements=[DN.dn_number])
            db.execute(insert_stmt, create_payloads)
        if update_payloads:
            db.bulk_update_mappings(DN, update_payloads)
        db.commit()
        dn_sync_logger.debug(
            "Persisted %d new and %d updated DN entries in %.3fs",
            created_count,
            updated_count,
            perf_counter() - db_start,
        )
        dn_sync_logger.info("Applied DN changes: created=%d, updated=%d", created_count, updated_count)
    else:
        dn_sync_logger.info("No DN sheet changes detected; skipping database write")

    dn_numbers_list = sorted(dn_numbers)
    reset_active_count = 0
    mark_deleted_count = 0

    if dn_numbers_list:
        reset_active_count = (
            db.query(DN)
            .filter(DN.dn_number.in_(dn_numbers_list))
            .filter(func.coalesce(DN.is_deleted, "N") != "N")
            .update({DN.is_deleted: "N"}, synchronize_session=False)
        )
        missing_q = db.query(DN).filter(~DN.dn_number.in_(dn_numbers_list))
    else:
        missing_q = db.query(DN)

    mark_deleted_count = missing_q.filter(func.coalesce(DN.is_deleted, "N") != "Y").update(
        {DN.is_deleted: "Y"}, synchronize_session=False
    )

    if reset_active_count or mark_deleted_count:
        db.commit()
        if reset_active_count:
            dn_sync_logger.info(
                "Reset is_deleted to 'N' for %d DN rows present in Google Sheet",
                reset_active_count,
            )
        if mark_deleted_count:
            dn_sync_logger.info(
                "Marked %d DN rows as deleted (missing from Google Sheet)",
                mark_deleted_count,
            )

    normalization_start = perf_counter()
    normalize_database_fields(db)
    dn_sync_logger.debug("normalize_database_fields completed in %.3fs", perf_counter() - normalization_start)

    dn_sync_logger.info(
        (
            "Completed sync_dn_sheet_to_db run: processed_rows=%d, valid_records=%d, "
            "unique_dns=%d, skipped_missing=%d, skipped_empty=%d, updated=%d, "
            "ignored=%d, duration=%.3fs"
        ),
        len(combined_df) if not combined_df.empty else 0,
        len(records),
        len(dn_numbers),
        skipped_missing_number,
        skipped_empty_payload,
        updated_count,
        unchanged_count,
        (datetime.utcnow() - start_time).total_seconds(),
    )

    return DnSyncResult(
        synced_numbers=dn_numbers_list,
        created_count=created_count,
        updated_count=updated_count,
        ignored_count=unchanged_count,
    )


def sync_dn_sheet_with_new_session() -> DnSyncResult:
    db = SessionLocal()
    try:
        try:
            result = sync_dn_sheet_to_db(db)
        except Exception as exc:
            dn_sync_logger.exception("sync_dn_sheet_to_db raised an error during manual trigger: %s", exc)
            create_dn_sync_log(
                db,
                status="failed",
                synced_numbers=None,
                message="Failed to sync DN data from Google Sheet",
                error_message=str(exc),
                error_traceback=traceback.format_exc(),
            )
            raise
        else:
            synced_numbers = result.synced_numbers
            message = (
                (
                    "Synced %d DN numbers from Google Sheet (created=%d, updated=%d, ignored=%d)"
                    % (len(synced_numbers), result.created_count, result.updated_count, result.ignored_count)
                )
                if synced_numbers
                else "Google Sheet returned no DN rows to sync"
            )
            create_dn_sync_log(db, status="success", synced_numbers=synced_numbers, message=message)
            return result
    finally:
        db.close()


async def run_dn_sheet_sync_once() -> DnSyncResult:
    return await asyncio.to_thread(sync_dn_sheet_with_new_session)


async def scheduled_dn_sheet_sync() -> None:
    try:
        result = await run_dn_sheet_sync_once()
        if result.synced_numbers:
            logger.info(
                "Synced %d DN numbers from Google Sheet (created=%d, updated=%d, ignored=%d)",
                len(result.synced_numbers),
                result.created_count,
                result.updated_count,
                result.ignored_count,
            )
    except Exception:
        logger.exception("Scheduled DN sheet sync failed")


def serialize_vehicle(vehicle: Vehicle) -> dict[str, Any]:
    return {
        "vehiclePlate": vehicle.vehicle_plate,
        "vehicleType": vehicle.vehicle_type,
        "driverName": vehicle.driver_name,
        "contactNumber": vehicle.contact_number,
        "LSP": vehicle.lsp,
        "status": vehicle.status,
        "arriveTime": to_gmt7_iso(vehicle.arrive_time),
        "departTime": to_gmt7_iso(vehicle.depart_time),
        "createdAt": to_gmt7_iso(vehicle.created_at),
        "updatedAt": to_gmt7_iso(vehicle.updated_at),
    }
