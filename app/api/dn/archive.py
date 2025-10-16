"""Archive old Plan MOS rows into a central Archive sheet.

This module provides an API endpoint that scans all worksheets whose
title starts with "Plan MOS" and moves qualifying rows into a single
archive worksheet. Data rows are assumed to start from row 4 (first
three rows are treated as header/meta rows).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, List, Optional, Dict

from fastapi import APIRouter, Body

from app.core.google import SPREADSHEET_URL, create_gspread_client
from app.core.sheet import fetch_plan_sheets, parse_date
from app.dn_columns import get_sheet_columns
from app.utils.time import TZ_GMT7
from app.utils.logging import logger

router = APIRouter(prefix="/api/dn")


@router.post("/archive/dry")
def archive_dry_run(
    threshold_days: int = Body(3, description="Lookback in days"),
    save_artifacts: bool = Body(False, description="If true, save dry-run report to tmp/ as JSON and Archive snapshot as Excel"),
) -> dict:
    """Dry-run: return which rows would be archived without modifying any sheets."""
    if threshold_days < 0:
        raise ValueError("threshold_days must be non-negative")

    gc = create_gspread_client()
    sh = gc.open_by_url(SPREADSHEET_URL)
    plan_sheets = fetch_plan_sheets(sh)
    sheet_columns = get_sheet_columns()

    now = datetime.now(TZ_GMT7)
    threshold_date = (now - timedelta(days=threshold_days)).date()

    report = {"threshold_days": threshold_days, "threshold_date": threshold_date.isoformat(), "sheets": []}
    total = 0
    total_archived = 0

    for ws in plan_sheets:
        try:
            values = ws.get_all_values()
        except Exception:
            logger.exception("Failed to fetch values for sheet %s", ws.title)
            continue
        if len(values) <= 3:
            continue

        partition = _partition_rows_for_sheet(values, sheet_columns, threshold_date)
        archived = partition["archive_rows"]
        total += len(partition["keep_rows"]) + len(archived)
        total_archived += len(archived)

        report["sheets"].append({"sheet": ws.title, "archived_count": len(archived), "archived_rows": archived})

    report["total_processed_rows"] = total
    report["total_archived_rows"] = total_archived

    result = {"ok": True, "report": report}

    # optionally save artifacts
    if save_artifacts:
        try:
            import time
            import json as _json
            from pathlib import Path
            Path("tmp").mkdir(exist_ok=True)
            ts = time.strftime('%Y%m%dT%H%M%S')
            path = Path('tmp')/f'dry_run_report_{ts}.json'
            with open(path, 'w', encoding='utf-8') as fh:
                _json.dump(result, fh, ensure_ascii=False, indent=2)
            # try export Archive sheet if exists
            try:
                import pandas as _pd
                archive_ws, existing_archive_values, _ = ensure_archive_sheet(gc.open_by_url(SPREADSHEET_URL), 'Archived', sheet_columns)
                if existing_archive_values:
                    df = _pd.DataFrame(existing_archive_values)
                    excel_path = Path('tmp')/f'archive_snapshot_{ts}.xlsx'
                    df.to_excel(excel_path, index=False, header=False)
                    result['artifact_paths'] = {'dry_run': str(path), 'archive_snapshot': str(excel_path)}
                else:
                    result['artifact_paths'] = {'dry_run': str(path)}
            except Exception:
                # still return the JSON path even if excel export fails
                result.setdefault('artifact_paths', {})['dry_run'] = str(path)
        except Exception:
            logger.exception('Failed to save dry-run artifacts')

    return result


@router.post("/archive")
def archive_plan_mos_rows(
    *,
    threshold_days: int = Body(
        3, description="Archive rows older than this many days"
    ),
    archive_sheet_name: str = Body(
        "Archived", description="Name of archive sheet"
    ),
    save_artifacts: bool = Body(False, description="If true, save archive summary and Archive snapshot to tmp/"),
) -> dict[str, Any]:
    """Traverse all 'Plan MOS' sheets and move qualifying rows to Archive.

    Criteria: plan_mos_date earlier than (now - threshold_days) AND
    (status_delivery == 'POD' OR status_site is 'REPLAN MOS' / 'CANCEL MOS').
    """
    if threshold_days < 0:
        raise ValueError("threshold_days must be non-negative")

    gc = create_gspread_client()
    sh = gc.open_by_url(SPREADSHEET_URL)
    plan_sheets = fetch_plan_sheets(sh)
    sheet_columns = get_sheet_columns()

    # Prepare or create the archive worksheet and get current values
    archive_ws, existing_archive_values, archive_header = ensure_archive_sheet(
        sh, archive_sheet_name, sheet_columns
    )

    now = datetime.now(TZ_GMT7)
    threshold_date = (now - timedelta(days=threshold_days)).date()

    total_processed = 0
    total_archived = 0
    processed_sheets: List[str] = []

    # collect value ranges for a single batch update
    value_data: List[dict] = []

    for ws in plan_sheets:
        sheet_start = datetime.now()
        try:
            values = ws.get_all_values()
        except Exception:
            logger.exception("Failed to fetch values for sheet %s", ws.title)
            continue

        if len(values) <= 3:
            continue

        try:
            partition = _partition_rows_for_sheet(values, sheet_columns, threshold_date)
        except Exception:
            logger.exception("Failed to partition rows for sheet %s", ws.title)
            continue

        header_rows = partition["header_rows"]
        keep_rows = partition["keep_rows"]
        archived_info = partition["archive_rows"]  # list of dicts with row & values

        # update counters
        total_processed += len(keep_rows) + len(archived_info)
        total_archived += len(archived_info)

        # prepare values for writing back to the plan sheet: header_rows + kept rows
        try:
            rows_to_write = list(header_rows) + list(keep_rows)
            rows_to_write = [list(r) for r in rows_to_write]
            value_data.append({
                "range": f"'{ws.title}'!A1",
                "values": rows_to_write,
            })
            processed_sheets.append(ws.title)
        except Exception:
            logger.exception("Failed to prepare rewrite payload for sheet %s", ws.title)

        # prepare archive rows values (append) for archive sheet
        if archived_info:
            try:
                archive_rows_values = [item["values"] + [ws.title, str(item["row"])] for item in archived_info]
                start_row = (len(existing_archive_values) if existing_archive_values else 0) + 1
                if start_row == 0:
                    start_row = 2
                value_data.append({
                    "range": f"'{archive_sheet_name}'!A{start_row}",
                    "values": archive_rows_values,
                })
                if existing_archive_values is None:
                    existing_archive_values = []
                existing_archive_values = existing_archive_values + archive_rows_values
            except Exception:
                logger.exception("Failed to prepare archive payload for %s", archive_sheet_name)

        # log basic per-sheet summary and duration
        try:
            sheet_duration = (datetime.now() - sheet_start).total_seconds()
            logger.info(
                "Archive: processed sheet=%s kept=%d archived=%d duration=%.2fs",
                ws.title,
                len(keep_rows),
                len(archived_info),
                sheet_duration,
            )
        except Exception:
            # Avoid failing the whole run for logging
            logger.exception("Failed to log summary for sheet %s", ws.title)

    # execute single batch update for all value writes
    if value_data:
        try:
            sh.batch_update({"valueInputOption": "USER_ENTERED", "data": value_data})
        except Exception:
            logger.exception("Batch update failed for archive operation; attempting per-sheet fallback")
            # Fallback: try per-sheet writes
            for item in value_data:
                try:
                    sh.values_update(item["range"], params={"valueInputOption": "USER_ENTERED"}, body={"values": item["values"]})
                except Exception:
                    logger.exception("Fallback write failed for range %s", item.get("range"))

    summary = {
        "threshold_days": threshold_days,
        "threshold_date": threshold_date.isoformat(),
        "processed_sheets": processed_sheets,
        "total_processed_rows": total_processed,
        "total_archived_rows": total_archived,
    }

    result = {"ok": True, "summary": summary}

    if save_artifacts:
        try:
            import time
            import json as _json
            from pathlib import Path
            Path('tmp').mkdir(exist_ok=True)
            ts = time.strftime('%Y%m%dT%H%M%S')
            summary_path = Path('tmp')/f'archive_summary_{ts}.json'
            with open(summary_path, 'w', encoding='utf-8') as fh:
                _json.dump(result, fh, ensure_ascii=False, indent=2)
            # export updated Archive sheet
            try:
                import pandas as _pd
                archive_ws, existing_archive_values, _ = ensure_archive_sheet(sh, archive_sheet_name, sheet_columns)
                if existing_archive_values:
                    df = _pd.DataFrame(existing_archive_values)
                    excel_path = Path('tmp')/f'archive_snapshot_after_{ts}.xlsx'
                    df.to_excel(excel_path, index=False, header=False)
                    result['artifact_paths'] = {'summary': str(summary_path), 'archive_snapshot': str(excel_path)}
                else:
                    result['artifact_paths'] = {'summary': str(summary_path)}
            except Exception:
                result.setdefault('artifact_paths', {})['summary'] = str(summary_path)
        except Exception:
            logger.exception('Failed to save archive artifacts')

    return result


def _partition_rows_for_sheet(values: list, sheet_columns: List[str], threshold_date) -> dict:
    """Return dict with keep_rows and archive_rows for a sheet given its values."""
    header_rows = values[:3]
    data_rows = values[3:]
    keep_rows: List[List[str]] = []
    archive_rows: List[List[str]] = []

    try:
        plan_idx = sheet_columns.index("plan_mos_date")
    except ValueError:
        raise RuntimeError("plan_mos_date column not in sheet columns")
    try:
        status_delivery_idx = sheet_columns.index("status_delivery")
    except ValueError:
        raise RuntimeError("status_delivery column not in sheet columns")
    status_site_idx = sheet_columns.index("status_site") if "status_site" in sheet_columns else None

    for row_offset, row in enumerate(data_rows, start=4):
        # normalize row into list of proper length
        if not row:
            normalized = [""] * len(sheet_columns)
        else:
            normalized = list(row[: len(sheet_columns)])
            if len(normalized) < len(sheet_columns):
                normalized = normalized + [""] * (len(sheet_columns) - len(normalized))

        plan_cell = normalized[plan_idx] if len(normalized) > plan_idx else ""
        status_delivery_cell = normalized[status_delivery_idx] if len(normalized) > status_delivery_idx else ""
        status_site_cell = normalized[status_site_idx] if status_site_idx is not None and len(normalized) > status_site_idx else ""

        # attempt to parse date only if plan_cell is present
        plan_date_value = None
        if plan_cell and str(plan_cell).strip():
            plan_date_value = parse_plan_date(plan_cell)

        should_archive = False
        if plan_date_value is not None and plan_date_value < threshold_date:
            sd = (status_delivery_cell or "").strip().upper()
            ss = (status_site_cell or "").strip().upper()
            if sd == "POD" or ss in ("REPLAN MOS", "CANCEL MOS"):
                should_archive = True

        if should_archive:
            archive_rows.append({
                "row": row_offset,
                "values": normalized,
                "plan_mos_date": plan_cell,
                "status_delivery": status_delivery_cell,
                "status_site": status_site_cell,
            })
        else:
            keep_rows.append(normalized)

    return {"header_rows": header_rows, "keep_rows": keep_rows, "archive_rows": archive_rows}


def parse_plan_date(plan_cell: str) -> Optional[datetime]:
    """Parse a plan_mos_date cell into a date (or None).

    Uses existing parse_date() then falls back to pandas.
    """
    if not plan_cell:
        return None
    parsed = parse_date(plan_cell)
    if isinstance(parsed, datetime):
        return parsed.date()
    try:
        import pandas as _pd

        pd_date = _pd.to_datetime(plan_cell, errors="coerce")
    except Exception:
        return None
    if pd_date is None or getattr(pd_date, "isnull", False):
        return None
    try:
        return pd_date.date()
    except Exception:
        return None


def ensure_archive_sheet(sh, archive_sheet_name: str, sheet_columns: List[str]) -> tuple:
    """Ensure archive worksheet exists and header is correct. Returns (worksheet, existing_values, header).

    existing_values will be a list of rows (possibly empty).
    """
    try:
        archive_ws = sh.worksheet(archive_sheet_name)
    except Exception:
        archive_ws = sh.add_worksheet(title=archive_sheet_name, rows="1000", cols=str(len(sheet_columns) + 2))

    archive_header = sheet_columns + ["source_sheet", "source_row"]
    try:
        existing_archive_values = archive_ws.get_all_values()
    except Exception:
        existing_archive_values = []

    if not existing_archive_values or not any(existing_archive_values):
        try:
            archive_ws.append_row(archive_header)
            existing_archive_values = [archive_header]
        except Exception:
            logger.exception("Failed to write header to archive sheet %s", archive_sheet_name)
            existing_archive_values = []
    else:
        current_header = existing_archive_values[0] if existing_archive_values else []
        if len(current_header) < len(archive_header) or current_header[: len(sheet_columns)] != sheet_columns:
            try:
                archive_ws.delete_rows(1)
                archive_ws.insert_row(archive_header, index=1)
                existing_archive_values[0:1] = [archive_header]
            except Exception:
                logger.exception("Failed to ensure header for archive sheet %s", archive_sheet_name)

    return archive_ws, existing_archive_values, archive_header


def prepare_plan_sheet_payload(ws_title: str, header_rows: List[List[str]], keep_rows: List[List[str]]) -> Dict[str, Any]:
    """Return a value_data dict entry to overwrite a plan sheet with header + kept rows."""
    rows_to_write = list(header_rows) + list(keep_rows)
    rows_to_write = [list(r) for r in rows_to_write]
    return {"range": f"'{ws_title}'!A1", "values": rows_to_write}


def prepare_archive_payload(archive_sheet_name: str, existing_archive_values: List[List[str]], archive_rows_values: List[List[str]]) -> tuple:
    """Return a value_data dict entry to append archive_rows_values to archive sheet and updated existing list."""
    start_row = (len(existing_archive_values) if existing_archive_values else 0) + 1
    if start_row == 0:
        start_row = 2
    payload = {"range": f"'{archive_sheet_name}'!A{start_row}", "values": archive_rows_values}
    updated_existing = (existing_archive_values or []) + archive_rows_values
    return payload, updated_existing
