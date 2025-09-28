# app/main.py
from fastapi import Body, FastAPI, UploadFile, File, Form, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional, List, Any
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, time, date
from time import perf_counter
import asyncio
import re, os, unicodedata
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from .settings import settings
from .db import Base, engine, get_db, SessionLocal
from .dn_columns import (
    extend_dn_columns as extend_dn_table_columns,
    get_sheet_columns,
    refresh_dynamic_columns,
    filter_assignable_dn_fields,
    get_mutable_dn_columns,
)
from .time_utils import to_gmt7_iso, parse_gmt7_date_range
from .crud import (
    ensure_dn,
    add_dn_record,
    list_dn_records,
    list_all_dn_records,
    search_dn_records,
    list_dn_records_by_dn_numbers,
    list_dn_by_dn_numbers,
    update_dn_record,
    delete_dn,
    delete_dn_record,
    get_existing_dn_numbers,
    get_latest_dn_records_map,
    get_dn_map_by_numbers,
    search_dn_list,
    create_dn_sync_log,
    get_latest_dn_sync_log,
    get_dn_unique_field_values,
    get_dn_status_delivery_counts,
    upsert_vehicle_signin,
    get_vehicle_by_plate,
    mark_vehicle_departed,
    list_vehicles as list_vehicle_entries,
)
from .storage import save_file
from fastapi.responses import JSONResponse, FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging, traceback
import gspread
import pandas as pd
from .models import DN, Vehicle
from sqlalchemy.dialects.postgresql import insert
from .logging_utils import logger

# ====== 启动与静态 ======
os.makedirs(settings.storage_disk_path, exist_ok=True)
app = FastAPI(title="DU Backend API", version="1.1.0")

GS_KEY_PATH = Path("/etc/secrets/gskey.json")
try:
    gs_key_content = GS_KEY_PATH.read_text(encoding="utf-8")
    logger.info("Loaded gskey.json content: %s", gs_key_content)
except FileNotFoundError:
    logger.debug("gskey.json not found at %s, skipping", GS_KEY_PATH)
except Exception as exc:
    logger.debug("Failed to read gskey.json from %s: %s", GS_KEY_PATH, exc)


def create_gspread_client() -> gspread.Client:
    """Create a gspread client using service account when available."""

    try:
        logger.debug(
            "Attempting to create gspread client using service account file at %s",
            GS_KEY_PATH,
        )
        gc = gspread.service_account(filename=str(GS_KEY_PATH))
        logger.info("Using gspread service account authentication")
        return gc
    except Exception as exc:
        logger.warning(
            "Failed to authenticate using service account at %s: %s. Falling back to API key.",
            GS_KEY_PATH,
            exc,
        )
        gc = gspread.api_key(API_KEY)
        logger.info("Using gspread API key authentication")
        return gc

DN_SYNC_LOG_PATH = Path(os.getenv("DN_SYNC_LOG_PATH", "/tmp/dn_sync.log")).expanduser()
DN_SYNC_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


class _DnSyncLogFilter(logging.Filter):
    """Filter ensuring only DN sync records reach the file handler."""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        return bool(getattr(record, "dn_sync", False))


_dn_sync_file_handler: logging.FileHandler | None = None


def _configure_dn_sync_logger(base_logger: logging.Logger) -> logging.LoggerAdapter:
    """Configure a DN sync logger that still propagates to the base logger."""

    global _dn_sync_file_handler

    if _dn_sync_file_handler is None or getattr(
        _dn_sync_file_handler, "baseFilename", None
    ) != str(DN_SYNC_LOG_PATH):
        handler = logging.FileHandler(DN_SYNC_LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        handler.setLevel(logging.DEBUG)
        handler.addFilter(_DnSyncLogFilter())
        base_logger.addHandler(handler)
        _dn_sync_file_handler = handler

    return logging.LoggerAdapter(base_logger, {"dn_sync": True})


dn_sync_logger = _configure_dn_sync_logger(logger)

SHEET_SYNC_INTERVAL_SECONDS = 300
_scheduler: AsyncIOScheduler | None = None
_SHEET_SYNC_JOB_ID = "dn_sheet_sync"

@app.exception_handler(Exception)
async def all_exception_handler(request, exc):
    logger.error("Unhandled error on %s %s\n%s",
                 request.method, request.url.path, traceback.format_exc())
    return JSONResponse(status_code=500, content={"ok": False, "error": "internal_error", "errorInfo":traceback.format_exc()})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.storage_driver != "s3":
    os.makedirs(settings.storage_disk_path, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=settings.storage_disk_path, check_dir=False), name="uploads")

Base.metadata.create_all(bind=engine)
refresh_dynamic_columns(engine)

TZ_GMT7 = timezone(timedelta(hours=7))


def to_gmt7_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(TZ_GMT7).isoformat()


def parse_gmt7_date_range(
    date_from: datetime | None, date_to: datetime | None
) -> tuple[datetime | None, datetime | None]:
    """Normalize incoming datetimes to GMT+7 day boundaries."""

    def _normalize(value: datetime | None, is_start: bool) -> datetime | None:
        if value is None:
            return None

        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        local_value = value.astimezone(TZ_GMT7)
        boundary_time = time(0, 0, 0) if is_start else time(23, 59, 59)
        localized = datetime.combine(
            local_value.date(), boundary_time, tzinfo=TZ_GMT7
        )
        return localized.astimezone(timezone.utc)

    start = _normalize(date_from, True)
    end = _normalize(date_to, False)

    return start, end

# ====== 校验与清洗 ======
DU_RE = re.compile(r"^.+$")
DN_RE = re.compile(r"^.+$")

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
    "TRANSPORTING FROM WH",
    "ARRIVED AT XD/PM",
    "TRANSPORTING FROM XD/PM",
    "ARRIVED AT SITE",
    "开始运输",
    "运输中",
    "已到达",
    "过夜",
)

VALID_STATUS_DESCRIPTION = ", ".join(VALID_STATUSES)

VEHICLE_VALID_STATUSES: tuple[str, ...] = ("arrived", "departed")

_ZERO_WIDTH_CHARS = "\u200b\ufeff"
_ZERO_WIDTH_TRANS = {ord(ch): None for ch in _ZERO_WIDTH_CHARS}


class DNColumnExtensionRequest(BaseModel):
    columns: List[str] = Field(
        ...,
        description="DN table columns to ensure exist",
        min_length=1,
    )


class VehicleSigninRequest(BaseModel):
    vehicle_plate: str = Field(..., alias="vehiclePlate")
    lsp: str = Field(..., alias="LSP")
    vehicle_type: str | None = Field(None, alias="vehicleType")
    driver_name: str | None = Field(None, alias="driverName")
    contact_number: str | None = Field(None, alias="contactNumber")
    arrive_time: datetime | None = Field(None, alias="arriveTime")

    class Config:
        populate_by_name = True


class VehicleDepartRequest(BaseModel):
    vehicle_plate: str = Field(..., alias="vehiclePlate")
    depart_time: datetime | None = Field(None, alias="departTime")

    class Config:
        populate_by_name = True


class ArchiveMarkRequest(BaseModel):
    threshold_days: int = Field(
        7,
        alias="thresholdDays",
        ge=0,
        description="Number of days before today that Plan MOS rows must precede to be archived.",
    )

    class Config:
        populate_by_name = True

@lru_cache(maxsize=4096)
def normalize_du(s: str) -> str:
    """NFC 规整、去零宽、去空白、统一大写"""
    if not s:
        return ""

    # NFC 规整
    s = unicodedata.normalize("NFC", s)

    # 去零宽、BOM字符
    s = s.translate(_ZERO_WIDTH_TRANS)

    # 去空白并统一为大写
    s = s.strip().upper()

    return s


def normalize_vehicle_plate(value: str) -> str:
    if not value:
        return ""

    return "".join(value.split()).upper()


def ensure_gmt7_timezone(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=TZ_GMT7)

    return dt


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


def normalize_dn(s: str) -> str:
    return normalize_du(s)


def _normalize_batch_dn_numbers(*value_lists: Optional[List[str]]) -> list[str]:
    raw_numbers: list[str] = []
    for values in value_lists:
        if not values:
            continue
        raw_numbers.extend(values)

    flat: list[str] = []
    for value in raw_numbers:
        if not value:
            continue
        for part in value.split(','):
            normalized = normalize_dn(part)
            if normalized:
                flat.append(normalized)

    numbers = [x for x in dict.fromkeys(flat) if x]

    if not numbers:
        raise HTTPException(status_code=400, detail="Missing dn_number")

    invalid = [x for x in numbers if not DN_RE.fullmatch(x)]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid DN number(s): {', '.join(invalid)}",
        )

    return numbers


def _collect_query_values(*values: Any) -> list[str] | None:
    """Normalize query values supporting repeated or comma-separated entries."""

    normalized: list[str] = []
    seen: set[str] = set()

    def _add_candidate(candidate: Any) -> None:
        if not isinstance(candidate, str):
            return

        parts = [candidate]
        if "," in candidate:
            parts = candidate.split(",")

        for part in parts:
            trimmed = part.strip()
            if not trimmed:
                continue
            if trimmed in seen:
                continue
            seen.add(trimmed)
            normalized.append(trimmed)

    for value in values:
        if value is None:
            continue

        if isinstance(value, str):
            _add_candidate(value)
            continue

        try:
            iterator = iter(value)
        except TypeError:
            continue

        for candidate in iterator:
            _add_candidate(candidate)

    return normalized or None


# ====== 基础健康检查 ======
@app.get("/")
def healthz():
    return {"ok": True, "message":"You can use admin panel now."}

# ====== DN 接口 ======


@app.post("/api/dn/columns/extend")
def extend_dn_columns_api(
    request: DNColumnExtensionRequest, db: Session = Depends(get_db)
):
    try:
        added = extend_dn_table_columns(db, request.columns)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "ok": True,
        "added_columns": added,
        "columns": get_sheet_columns(),
    }


def sync_delivery_status_to_sheet(
    sheet_name: str,
    row_index: int,
    dn_number: str,
    new_value: str,
) -> dict[str, Any] | None:
    """Update the delivery status in Google Sheets for the provided DN."""

    column_names = get_sheet_columns()

    try:
        status_column_position = column_names.index("status_delivery") + 1
    except ValueError:
        return {
            "sheet": sheet_name,
            "row": row_index,
            "column_name": "status_delivery",
            "error": "status_delivery column not found in sheet definition",
            "new_value": new_value,
        }

    try:
        dn_column_position = column_names.index("dn_number") + 1
    except ValueError:
        return {
            "sheet": sheet_name,
            "row": row_index,
            "column_name": "dn_number",
            "error": "dn_number column not found in sheet definition",
            "new_value": new_value,
        }

    try:
        gc = create_gspread_client()
        sh = gc.open_by_url(SPREADSHEET_URL)
        worksheet = sh.worksheet(sheet_name)
        dn_cell_value = worksheet.cell(row_index, dn_column_position).value
        normalized_sheet_dn = normalize_dn(dn_cell_value or "")

        if normalized_sheet_dn != dn_number:
            search_details: dict[str, Any] = {
                "sheet": sheet_name,
                "row": row_index,
                "column": status_column_position,
                "column_name": "status_delivery",
                "current_dn_number": dn_cell_value,
                "expected_dn_number": dn_number,
                "error": "dn_number mismatch for target row",
                "new_value": new_value,
            }
            try:
                dn_column_values = worksheet.col_values(dn_column_position)
            except Exception as search_exc:
                search_details["search_error"] = str(search_exc)
                return search_details

            found_row_index: int | None = None
            for idx, value in enumerate(dn_column_values, start=1):
                if normalize_dn(value or "") == dn_number:
                    found_row_index = idx
                    break

            if found_row_index is None:
                search_details["search_result"] = "dn_number not found in sheet"
                return search_details

            try:
                cell = worksheet.cell(found_row_index, status_column_position)
            except Exception as fetch_exc:
                search_details["search_row"] = found_row_index
                search_details["search_error"] = str(fetch_exc)
                return search_details

            update_details = {
                "sheet": sheet_name,
                "row": found_row_index,
                "column": status_column_position,
                "column_name": "status_delivery",
                "current_value": cell.value,
                "new_value": new_value,
                "found_via_search": True,
                "original_row_mismatch": row_index,
            }

            normalized_current_value = (cell.value or "").strip()
            if normalized_current_value == new_value:
                update_details["skipped"] = True
                update_details["normalized_current_value"] = normalized_current_value
                return update_details

            try:
                worksheet.update_cell(found_row_index, status_column_position, new_value)
            except Exception as update_exc:
                update_details["update_error"] = str(update_exc)
                return update_details

            update_details["updated"] = True
            return update_details

        try:
            cell = worksheet.cell(row_index, status_column_position)
        except Exception as fetch_exc:
            return {
                "sheet": sheet_name,
                "row": row_index,
                "column": status_column_position,
                "column_name": "status_delivery",
                "error": str(fetch_exc),
                "new_value": new_value,
            }

        update_details = {
            "sheet": sheet_name,
            "row": row_index,
            "column": status_column_position,
            "column_name": "status_delivery",
            "current_value": cell.value,
            "new_value": new_value,
        }

        normalized_current_value = (cell.value or "").strip()
        if normalized_current_value == new_value:
            update_details["skipped"] = True
            update_details["normalized_current_value"] = normalized_current_value
            return update_details

        try:
            worksheet.update_cell(row_index, status_column_position, new_value)
        except Exception as update_exc:
            update_details["update_error"] = str(update_exc)
            return update_details

        update_details["updated"] = True
        return update_details
    except Exception as exc:
        return {
            "sheet": sheet_name,
            "row": row_index,
            "column": status_column_position,
            "column_name": "status_delivery",
            "error": str(exc),
            "new_value": new_value,
        }

    return None


def mark_plan_mos_rows_for_archiving(
    threshold_days: int | None = None,
) -> dict[str, Any]:
    """Mark outdated POD deliveries for archiving in Google Sheets."""

    if threshold_days is None:
        threshold_days = DEFAULT_ARCHIVE_THRESHOLD_DAYS

    if threshold_days < 0:
        raise ValueError("threshold_days must be non-negative")

    column_names = get_sheet_columns()

    try:
        plan_mos_index = column_names.index("plan_mos_date")
    except ValueError as exc:
        raise RuntimeError("plan_mos_date column not found in sheet definition") from exc

    try:
        status_delivery_index = column_names.index("status_delivery")
    except ValueError as exc:
        raise RuntimeError("status_delivery column not found in sheet definition") from exc

    threshold_date = (datetime.now(TZ_GMT7) - timedelta(days=threshold_days)).date()
    logger.info(
        "Marking rows for archiving where plan_mos_date is before %s and status_delivery is POD",
        threshold_date.isoformat(),
    )

    gc = create_gspread_client()
    sh = gc.open_by_url(SPREADSHEET_URL)
    plan_sheets = fetch_plan_sheets(sh)
    sheet_titles = [sheet.title for sheet in plan_sheets]

    matched_rows = 0
    formatted_rows = 0
    affected_rows: List[dict[str, Any]] = []
    pending_requests: List[dict[str, Any]] = []

    def flush_requests() -> None:
        if not pending_requests:
            return
        sh.batch_update({"requests": list(pending_requests)})
        pending_requests.clear()

    for sheet in plan_sheets:
        values = sheet.get_all_values()
        if len(values) <= 3:
            continue

        sheet_column_count = getattr(sheet, "col_count", None)
        if not sheet_column_count:
            sheet_column_count = max((len(row) for row in values), default=0)
        effective_color_range_end = max(sheet_column_count, 0)

        for row_offset, row_values in enumerate(values[3:], start=4):
            if not row_values:
                continue

            # Skip rows without meaningful content to avoid touching empty tails.
            if not any((cell or "").strip() for cell in row_values):
                continue

            plan_cell = row_values[plan_mos_index] if len(row_values) > plan_mos_index else ""
            status_cell = row_values[status_delivery_index] if len(row_values) > status_delivery_index else ""

            if not plan_cell:
                continue

            parsed_plan = parse_date(plan_cell)
            plan_date_value: date | None = None
            if isinstance(parsed_plan, datetime):
                plan_date_value = parsed_plan.date()
            else:
                try:
                    pandas_date = pd.to_datetime(plan_cell, errors="coerce")
                except Exception:
                    pandas_date = None
                if pandas_date is not None and not pd.isna(pandas_date):
                    plan_date_value = pandas_date.date()

            if plan_date_value is None:
                continue

            if plan_date_value >= threshold_date:
                continue

            normalized_status = (status_cell or "").strip().upper()
            if normalized_status != "POD":
                continue

            matched_rows += 1
            row_number = row_offset
            row_start_index = row_number - 1

            entry: dict[str, Any] = {
                "sheet": sheet.title,
                "row": row_number,
                "plan_mos_date": plan_cell,
                "status_delivery": status_cell,
            }
            if effective_color_range_end > 0:
                pending_requests.append(
                    {
                        "repeatCell": {
                            "range": {
                                "sheetId": sheet.id,
                                "startRowIndex": row_start_index,
                                "endRowIndex": row_start_index + 1,
                                "startColumnIndex": 0,
                                "endColumnIndex": effective_color_range_end,
                            },
                            "cell": {
                                "userEnteredFormat": {
                                    "textFormat": {
                                        "foregroundColor": ARCHIVE_TEXT_COLOR
                                    }
                                }
                            },
                            "fields": "userEnteredFormat.textFormat.foregroundColor",
                        }
                    }
                )
                formatted_rows += 1
                entry["formatted"] = True
            else:
                entry["formatted"] = False
                entry["formatting_skipped"] = True
            affected_rows.append(entry)

            # Flush periodically to respect Google Sheets API batch limits.
            if len(pending_requests) >= 90:
                flush_requests()

    flush_requests()

    logger.info(
        "Matched %d rows for archiving criteria; formatted %d rows",
        matched_rows,
        formatted_rows,
    )

    return {
        "threshold_days": threshold_days,
        "threshold_date": threshold_date.isoformat(),
        "matched_rows": matched_rows,
        "formatted_rows": formatted_rows,
        "sheets_processed": sheet_titles,
        "affected_rows": affected_rows,
    }


@app.post("/api/dn/archive/mark")
def mark_archive_rows_api(
    payload: ArchiveMarkRequest | None = Body(
        None,
        description="Optional configuration for Plan MOS archiving.",
    )
):
    threshold_days = (
        payload.threshold_days if payload is not None else DEFAULT_ARCHIVE_THRESHOLD_DAYS
    )

    try:
        result = mark_plan_mos_rows_for_archiving(threshold_days=threshold_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to mark rows for archiving: %s", exc)
        raise HTTPException(status_code=500, detail="failed_to_mark_archive_rows")

    return {"ok": True, "data": result}


@app.post("/api/dn/update")
def update_dn(
    dnNumber: str = Form(...),
    status: str = Form(...),
    delivery_status: str | None = Form(None),
    status_delivery: str | None = Form(
        None,
        description="Backward compatibility alias for delivery_status form field",
    ),
    remark: str | None = Form(None),
    duId: str | None = Form(None),
    photo: UploadFile | None = File(None),
    lng: str | float | None = Form(None),
    lat: str | float | None = Form(None),
    updated_by: str | None = Form(None),
    db: Session = Depends(get_db),
):
    dn_number = normalize_dn(dnNumber)
    if not DN_RE.fullmatch(dn_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    du_id_normalized = None
    if duId:
        du_id_normalized = normalize_du(duId)
        if not DU_RE.fullmatch(du_id_normalized):
            raise HTTPException(status_code=400, detail="Invalid DU ID")

    photo_url = None
    if photo and photo.filename:
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    lng_val = str(lng) if lng else None
    lat_val = str(lat) if lat else None

    updated_by_value = None
    if updated_by is not None:
        updated_by_value = updated_by.strip()
        if updated_by_value == "":
            updated_by_value = None

    delivery_status_raw = delivery_status
    if delivery_status_raw is None and status_delivery is not None:
        delivery_status_raw = status_delivery

    delivery_status_value = None
    if delivery_status_raw is not None:
        delivery_status_value = delivery_status_raw.strip()
        if delivery_status_value == "":
            delivery_status_value = None

    if delivery_status_value is None:
        delivery_status_value = "On Site" if status == "ARRIVED AT SITE" else "On The Way"

    existing_dn = db.query(DN).filter(DN.dn_number == dn_number).one_or_none()
    gs_sheet_name = existing_dn.gs_sheet if existing_dn is not None else None
    raw_gs_row = existing_dn.gs_row if existing_dn is not None else None
    gs_row_index: int | None
    if isinstance(raw_gs_row, int):
        gs_row_index = raw_gs_row
    elif isinstance(raw_gs_row, str):
        try:
            gs_row_index = int(raw_gs_row)
        except ValueError:
            gs_row_index = None
    else:
        gs_row_index = None

    ensure_payload: dict[str, Any] = {
        "du_id": du_id_normalized,
        "status": status,
        "remark": remark,
        "photo_url": photo_url,
        "lng": lng_val,
        "lat": lat_val,
    }
    if delivery_status_value is not None:
        ensure_payload["status_delivery"] = delivery_status_value
    if updated_by_value is not None:
        ensure_payload["last_updated_by"] = updated_by_value

    ensure_dn(
        db,
        dn_number,
        **ensure_payload,
    )

    rec = add_dn_record(
        db,
        dn_number=dn_number,
        status=status,
        remark=remark,
        photo_url=photo_url,
        lng=lng_val,
        lat=lat_val,
        du_id=du_id_normalized,
        updated_by=updated_by_value,
    )

    gspread_update_result: dict[str, Any] | None = None
    should_check_sheet = (
        gs_sheet_name
        and isinstance(gs_row_index, int)
        and gs_row_index > 0
        and delivery_status_value is not None
    )

    if should_check_sheet and delivery_status_value is not None:
        gspread_update_result = sync_delivery_status_to_sheet(
            gs_sheet_name,
            gs_row_index,
            dn_number,
            delivery_status_value,
        )

    response: dict[str, Any] = {"ok": True, "id": rec.id, "photo": photo_url}
    if gspread_update_result is not None:
        response["delivery_status_update_result"] = gspread_update_result

    return response


@app.post("/api/dn/batch_update")
def batch_update_dn(
    dn_numbers: List[str] = Body(..., description="JSON array of DN numbers to create"),
    db: Session = Depends(get_db),
):
    if not dn_numbers:
        return {
            "status": "fail",
            "errmessage": "DN number 列表为空",
            "success_count": 0,
            "failure_count": 0,
            "success_dn_numbers": [],
            "failure_details": {},
        }

    normalized_numbers: List[str] = []
    failure_details: dict[str, str] = {}
    seen_numbers: set[str] = set()

    def add_failure(number: str, reason: str) -> None:
        failure_details[number] = reason

    for raw_number in dn_numbers:
        normalized = normalize_dn(raw_number)
        if not normalized or not DN_RE.fullmatch(normalized):
            base_key = raw_number if isinstance(raw_number, str) and raw_number else "<empty>"
            failure_key = str(base_key) if base_key is not None else "<empty>"
            add_failure(failure_key, "无效的 DN number")
            continue
        if normalized in seen_numbers:
            add_failure(normalized, "请求中重复")
            continue
        seen_numbers.add(normalized)
        normalized_numbers.append(normalized)

    existing_numbers = get_existing_dn_numbers(db, normalized_numbers)
    success_numbers: List[str] = []

    for number in normalized_numbers:
        if number in existing_numbers:
            add_failure(number, "DN number 已存在")
            continue

        ensure_dn(db, number, status="NO STATUS")
        add_dn_record(
            db,
            dn_number=number,
            status="NO STATUS",
            remark=None,
            photo_url=None,
            lng=None,
            lat=None,
        )
        success_numbers.append(number)

    status_value = "ok" if success_numbers else "fail"

    return {
        "status": status_value,
        "success_count": len(success_numbers),
        "failure_count": len(failure_details),
        "success_dn_numbers": success_numbers,
        "failure_details": failure_details,
    }


@app.get("/api/dn/search")
def search_dn_records_api(
    dn_number: Optional[str] = Query(None, description="精确 DN number"),
    du_id: Optional[str] = Query(None, description="关联 DU ID"),
    status: Optional[str] = Query(None, description=f"状态过滤，可选: {VALID_STATUS_DESCRIPTION}"),
    remark: Optional[str] = Query(None, description="备注关键词(模糊)"),
    has_photo: Optional[bool] = Query(None, description="是否必须带附件 true/false"),
    date_from: Optional[datetime] = Query(None, description="起始时间(ISO 8601)"),
    date_to: Optional[datetime] = Query(None, description="结束时间(ISO 8601)"),
    page: int = Query(1, ge=1),
    page_size: Optional[int] = Query(
        None, ge=1, description="每页数量，缺省时返回全部符合条件的数据"
    ),
    db: Session = Depends(get_db),
):
    if dn_number:
        dn_number = normalize_dn(dn_number)
        if not DN_RE.fullmatch(dn_number):
            raise HTTPException(status_code=400, detail=f"Invalid DN number: {dn_number}")
    if du_id:
        du_id = normalize_du(du_id)
        if not DU_RE.fullmatch(du_id):
            raise HTTPException(status_code=400, detail=f"Invalid DU ID: {du_id}")

    total, items = search_dn_records(
        db,
        dn_number=dn_number,
        du_id=du_id,
        status=status,
        remark_keyword=remark,
        has_photo=has_photo,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size if page_size is not None else len(items),
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
                "status": it.status,
                "remark": it.remark,
                "photo_url": it.photo_url,
                "lng": it.lng,
                "lat": it.lat,
                "updated_by": it.updated_by,
                "created_at": to_gmt7_iso(it.created_at),
            }
            for it in items
        ],
    }


@app.get("/api/dn/batch")
def batch_get_dn_records(
    dn_number: Optional[List[str]] = Query(None, description="重复 dn_number 或逗号分隔"),
    dnnumber_legacy: Optional[List[str]] = Query(
        None,
        alias="dnnumber",
        description="重复 dn_number 或逗号分隔 (legacy alias)",
        include_in_schema=False,
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    dn_numbers = _normalize_batch_dn_numbers(dn_number, dnnumber_legacy)

    total, items = list_dn_records_by_dn_numbers(
        db, dn_numbers, page=page, page_size=page_size
    )
    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
                "status": it.status,
                "remark": it.remark,
                "photo_url": it.photo_url,
                "lng": it.lng,
                "lat": it.lat,
                "updated_by": it.updated_by,
                "created_at": to_gmt7_iso(it.created_at),
            }
            for it in items
        ],
    }


@app.put("/api/dn/update/{id}")
def edit_dn_record(
    id: int,
    status: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    duId: Optional[str] = Form(None),
    updated_by: Optional[str] = Form(None),
    photo: UploadFile | None = File(None),
    json_body: Optional[dict] = Body(None),
    db: Session = Depends(get_db),
):
    du_id_provided = duId is not None
    updated_by_provided = updated_by is not None

    if json_body and isinstance(json_body, dict):
        if "status" in json_body:
            status = json_body.get("status")
        if "remark" in json_body:
            remark = json_body.get("remark")
        if "duId" in json_body:
            duId = json_body.get("duId")
            du_id_provided = True
        if "updated_by" in json_body:
            updated_by = json_body.get("updated_by")
            updated_by_provided = True

    if status is not None and status.strip() == "":
        status = None
    if remark is not None:
        remark = remark.strip()
        if remark == "":
            remark = None
        elif len(remark) > 1000:
            raise HTTPException(status_code=400, detail="remark too long (max 1000 chars)")

    if duId is not None:
        duId = duId.strip()
        if duId == "":
            duId = None
        else:
            normalized_candidate = normalize_du(duId)
            if not DU_RE.fullmatch(normalized_candidate):
                raise HTTPException(status_code=400, detail="Invalid DU ID")
            duId = normalized_candidate
    elif du_id_provided:
        duId = None

    if isinstance(updated_by, str):
        updated_by = updated_by.strip()
        if updated_by == "":
            updated_by = None
    elif updated_by_provided and updated_by is not None:
        updated_by = str(updated_by)

    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    photo_url = None
    if photo and getattr(photo, "filename", None):
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    normalized_du_id = duId if duId else None

    rec = update_dn_record(
        db,
        rec_id=id,
        status=status,
        remark=remark,
        photo_url=photo_url,
        du_id=normalized_du_id,
        du_id_set=du_id_provided,
        updated_by=updated_by,
        updated_by_set=updated_by_provided,
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")

    ensure_payload: dict[str, Any] = {
        "du_id": rec.du_id,
        "status": rec.status,
        "remark": rec.remark,
        "photo_url": rec.photo_url,
        "lng": rec.lng,
        "lat": rec.lat,
    }
    if updated_by_provided:
        ensure_payload["last_updated_by"] = rec.updated_by

    ensure_dn(
        db,
        rec.dn_number,
        **ensure_payload,
    )

    return {"ok": True, "id": rec.id}


@app.delete("/api/dn/update/{id}")
def remove_dn_record(id: int, db: Session = Depends(get_db)):
    ok = delete_dn_record(db, id)
    if not ok:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"ok": True}


# 设置全局变量
API_KEY = "AIzaSyCxIBYFpNlPvQUXY83S559PEVXoagh8f88"
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/13-D-KkkbilYmlcHHa__CZkE2xtynL--ZxekZG4lWRic/edit?gid=1258103322#gid=1258103322"


ARCHIVE_TEXT_COLOR = {"red": 0.6, "green": 0.6, "blue": 0.6}
DEFAULT_ARCHIVE_THRESHOLD_DAYS = 7

MONTH_MAP = {
    "Sept": "Sep",  # 'Sept' -> 'Sep'
}

DATE_FORMATS = [
    "%d %b %y",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d%b",
    "%d %b %y",
    "%d %b %Y",
    "%Y/%m/%d",
]

def fetch_plan_sheets(sheet_url):
    """获取以 'Plan MOS' 开头的所有工作表"""
    start = perf_counter()
    sheets = sheet_url.worksheets()
    dn_sync_logger.debug(
        "Fetched %d worksheets in %.3fs",
        len(sheets),
        perf_counter() - start,
    )
    dn_sync_logger.debug("Spreadsheet has %d worksheets", len(sheets))
    plan_sheets = [sheet for sheet in sheets if sheet.title.startswith("Plan MOS")]
    dn_sync_logger.debug(
        "Filtered plan sheets in %.3fs", perf_counter() - start
    )
    if plan_sheets:
        sheet_titles = [sheet.title for sheet in plan_sheets]
        preview_titles = ", ".join(sheet_titles[:3])
        if len(sheet_titles) > 3:
            preview_titles = f"{preview_titles}, ..."
        dn_sync_logger.info(
            "Found %d 'Plan MOS' sheets to sync (%s)",
            len(plan_sheets),
            preview_titles,
        )
    else:
        dn_sync_logger.info("No 'Plan MOS' sheets available for syncing")
    dn_sync_logger.debug(
        "Filtered %d plan sheets: %s",
        len(plan_sheets),
        [sheet.title for sheet in plan_sheets],
    )
    return plan_sheets

@lru_cache(maxsize=2048)
def parse_date(date_str: str):
    """解析日期字符串"""
    if date_str is None:
        return None

    if isinstance(date_str, datetime):
        return date_str

    if not isinstance(date_str, str):
        return date_str

    normalized = date_str

    # 替换月份简写
    for incorrect, correct in MONTH_MAP.items():
        normalized = normalized.replace(incorrect, correct)

    trimmed = normalized.strip()

    # 处理日期字符串
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(trimmed, fmt)
        except ValueError:
            continue

    return normalized


def normalize_database_fields(db: Session) -> None:
    """规范数据库中需要标准化的字段。"""

    dn_sync_logger.debug("Starting database field normalization")

    dn_entries = db.query(DN).filter(DN.plan_mos_date.isnot(None)).all()
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

    status_entries = (
        db.query(DN)
        .filter(
            or_(
                DN.status_delivery.is_(None),
                func.trim(DN.status_delivery) == "",
            )
        )
        .all()
    )
    normalized_status_delivery = 0

    for entry in status_entries:
        entry.status_delivery = "No Status"
        normalized_status_delivery += 1

    if normalized_plan_dates or normalized_status_delivery:
        db.commit()

    if normalized_plan_dates:
        dn_sync_logger.info(
            "Normalized plan_mos_date for %d DN rows", normalized_plan_dates
        )
    else:
        dn_sync_logger.debug("No plan_mos_date values required normalization")

    if normalized_status_delivery:
        dn_sync_logger.info(
            "Normalized status_delivery for %d DN rows", normalized_status_delivery
        )
    else:
        dn_sync_logger.debug("No status_delivery values required normalization")

def process_sheet_data(sheet, columns: List[str]) -> pd.DataFrame:
    """处理工作表数据"""
    fetch_start = perf_counter()
    all_values = sheet.get_all_values()
    dn_sync_logger.debug(
        "sheet.get_all_values for '%s' returned %d rows in %.3fs",
        sheet.title,
        len(all_values),
        perf_counter() - fetch_start,
    )
    dn_sync_logger.debug(
        "Processing sheet '%s' with %d total rows", sheet.title, len(all_values)
    )
    data = all_values[3:]  # 从第4行开始
    trimmed: List[List[str]] = []
    row_numbers: List[int] = []
    column_count = len(columns)
    for index, row in enumerate(data, start=4):
        row_values = row[:column_count]
        if len(row_values) < column_count:
            row_values = row_values + [""] * (column_count - len(row_values))
        trimmed.append(row_values)
        row_numbers.append(index)

    df = pd.DataFrame(trimmed, columns=columns)
    df["gs_sheet"] = sheet.title
    df["gs_row"] = row_numbers
    dn_sync_logger.debug(
        "Sheet '%s' produced DataFrame with %d rows", sheet.title, len(df)
    )
    dn_sync_logger.debug(
        "Processed sheet '%s' into DataFrame in %.3fs",
        sheet.title,
        perf_counter() - fetch_start,
    )
    return df

def process_all_sheets(sh) -> pd.DataFrame:
    """处理所有符合条件的工作表并合并数据"""
    total_start = perf_counter()
    plan_sheets = fetch_plan_sheets(sh)
    columns_start = perf_counter()
    columns = get_sheet_columns()
    dn_sync_logger.debug(
        "Loaded sheet column definitions in %.3fs",
        perf_counter() - columns_start,
    )
    processing_start = perf_counter()
    all_data = [process_sheet_data(sheet, columns) for sheet in plan_sheets]
    dn_sync_logger.debug(
        "Processed %d plan sheets in %.3fs",
        len(plan_sheets),
        perf_counter() - processing_start,
    )
    if not all_data:
        dn_sync_logger.info("No plan sheets found to process; returning empty DataFrame")
        return pd.DataFrame(columns=columns)
    combined = pd.concat(all_data, ignore_index=True)
    dn_sync_logger.info(
        "Combined sheet data into DataFrame with %d rows", len(combined)
    )
    dn_sync_logger.debug(
        "Combined DataFrame has %d rows and %d columns",
        len(combined),
        len(combined.columns),
    )
    dn_sync_logger.debug(
        "Completed sheet processing workflow in %.3fs",
        perf_counter() - total_start,
    )
    return combined


def normalize_sheet_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if pd.isna(value):
        return None
    return value


@dataclass
class DnSyncResult:
    """Aggregate information about a DN sheet synchronisation run."""

    synced_numbers: List[str]
    created_count: int
    updated_count: int
    ignored_count: int


def _values_match(existing_value: Any, new_value: Any) -> bool:
    """Return True if the database value already matches the incoming value."""

    if existing_value is None and new_value is None:
        return True

    if isinstance(existing_value, str):
        existing_value = existing_value.strip()
        if not existing_value:
            existing_value = None

    if isinstance(new_value, str):
        new_value = new_value.strip()
        if not new_value:
            new_value = None

    return existing_value == new_value


def sync_dn_sheet_to_db(db: Session) -> DnSyncResult:
    """同步 Google Sheet 中的 DN 数据到数据库。

    返回同步结果对象，包含按字典序排列的 DN number、
    新增、更新及忽略的数量。
    """
    start_time = datetime.utcnow()
    dn_sync_logger.info("Starting sync_dn_sheet_to_db run")

    try:
        dn_sync_logger.debug("Creating gspread client")
        client_start = perf_counter()
        gc = create_gspread_client()
        dn_sync_logger.debug(
            "Created gspread client in %.3fs", perf_counter() - client_start
        )
        dn_sync_logger.debug("Opening spreadsheet URL: %s", SPREADSHEET_URL)
        open_start = perf_counter()
        sh = gc.open_by_url(SPREADSHEET_URL)
        dn_sync_logger.debug(
            "Spreadsheet opened successfully in %.3fs",
            perf_counter() - open_start,
        )
        sheet_start = perf_counter()
        combined_df = process_all_sheets(sh)
        dn_sync_logger.debug(
            "Fetched and combined sheet data in %.3fs",
            perf_counter() - sheet_start,
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
    dn_sync_logger.debug("DataFrame contains %d total rows", total_rows)

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

        plan_mos_index = None
        if "plan_mos_date" in sheet_columns:
            plan_mos_index = sheet_columns.index("plan_mos_date")

        if dn_index is not None:
            for row_values in combined_df.itertuples(index=False, name=None):
                rows_iterated += 1
                row_normalization_start = perf_counter()
                normalized_row: list[Any] = []
                has_payload = False

                for idx, raw_value in enumerate(row_values):
                    normalized_value = normalize_sheet_value(raw_value)
                    if (
                        plan_mos_index is not None
                        and idx == plan_mos_index
                        and isinstance(normalized_value, str)
                        and normalized_value
                    ):
                        parse_start = perf_counter()
                        parsed_plan_mos_date = parse_date(normalized_value)
                        parse_duration = perf_counter() - parse_start
                        plan_mos_parse_total += parse_duration
                        plan_mos_parse_count += 1
                        if isinstance(parsed_plan_mos_date, datetime):
                            normalized_value = parsed_plan_mos_date.strftime("%d %b %y")

                    if idx != dn_index and normalized_value is not None:
                        has_payload = True

                    normalized_row.append(normalized_value)

                row_normalization_total += perf_counter() - row_normalization_start

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
                records.append(cleaned)
                record_build_total += perf_counter() - record_build_start
                rows_persisted += 1
                dn_numbers.add(normalized_number)
    else:
        dn_sync_logger.info("Combined DataFrame is empty; no rows to process")

    if not dn_numbers:
        dn_sync_logger.info(
            "No DN numbers extracted (skipped_missing=%d, skipped_empty=%d)",
            skipped_missing_number,
            skipped_empty_payload,
        )
        return []

    latest_records_for_update = get_latest_dn_records_map(db, dn_numbers)
    existing_dn_map = get_dn_map_by_numbers(db, dn_numbers)
    mutable_columns = set(get_mutable_dn_columns())
    dn_sync_logger.debug(
        "Fetched %d existing DN records for potential update", len(latest_records_for_update)
    )

    create_payload_by_number: dict[str, dict[str, Any]] = {}
    update_payload_by_number: dict[str, dict[str, Any]] = {}
    numbers_to_create: set[str] = set()
    numbers_to_update: set[str] = set()
    numbers_unchanged: set[str] = set()

    assignable_filter_total = 0.0
    non_null_filter_total = 0.0
    change_detection_total = 0.0
    payload_mutation_total = 0.0
    latest_merge_total = 0.0
    created_columns: set[str] = set()
    updated_columns: set[str] = set()
    created_field_total = 0
    updated_field_total = 0

    for entry in records:
        number = entry["dn_number"]
        sheet_fields = {
            key: entry.get(key) for key in sheet_columns if key != "dn_number"
        }
        latest = latest_records_for_update.get(number)
        existing_dn = existing_dn_map.get(number)
        if latest:
            merge_start = perf_counter()
            if not sheet_fields.get("du_id") and latest.du_id:
                sheet_fields["du_id"] = latest.du_id
            sheet_fields.update(
                {
                    "status": latest.status,
                    "remark": latest.remark,
                    "photo_url": latest.photo_url,
                    "lng": latest.lng,
                    "lat": latest.lat,
                }
            )
            latest_merge_total += perf_counter() - merge_start
        elif not existing_dn and number not in numbers_to_create:
            dn_sync_logger.debug("Preparing creation for DN %s from sheet data", number)

        assignable_start = perf_counter()
        assignable_fields = filter_assignable_dn_fields(
            sheet_fields, mutable_columns
        )
        assignable_filter_total += perf_counter() - assignable_start

        non_null_start = perf_counter()
        non_null_fields = {
            key: value for key, value in assignable_fields.items() if value is not None
        }
        non_null_filter_total += perf_counter() - non_null_start
        if not non_null_fields:
            continue

        comparison_start = perf_counter()
        if existing_dn:
            changed_fields: dict[str, Any] = {}
            for key, value in non_null_fields.items():
                if not _values_match(getattr(existing_dn, key, None), value):
                    changed_fields[key] = value
            change_detection_total += perf_counter() - comparison_start
            if not changed_fields:
                numbers_unchanged.add(number)
                continue

            if number not in numbers_to_update:
                dn_sync_logger.debug(
                    "Preparing update for existing DN %s after detecting differences", number
                )
            numbers_to_update.add(number)
            updated_columns.update(changed_fields.keys())
            payload = update_payload_by_number.setdefault(
                number, {"id": existing_dn.id, "dn_number": number}
            )
            mutation_start = perf_counter()
            payload.update(changed_fields)
            payload_mutation_total += perf_counter() - mutation_start
            updated_field_total += len(changed_fields)
        else:
            change_detection_total += perf_counter() - comparison_start
            numbers_to_create.add(number)
            created_columns.update(non_null_fields.keys())
            payload = create_payload_by_number.setdefault(
                number, {"dn_number": number}
            )
            mutation_start = perf_counter()
            payload.update(non_null_fields)
            payload_mutation_total += perf_counter() - mutation_start
            created_field_total += len(non_null_fields)

    if rows_iterated:
        dn_sync_logger.info(
            (
                "Row processing timing: total_normalization=%.3fs (avg %.6fs over %d rows), "
                "plan_mos_parse=%.3fs (%d calls), dn_normalization=%.3fs (avg %.6fs), "
                "record_build=%.3fs (avg %.6fs over %d persisted)"
            ),
            row_normalization_total,
            row_normalization_total / rows_iterated,
            rows_iterated,
            plan_mos_parse_total,
            plan_mos_parse_count,
            dn_normalization_total,
            dn_normalization_total / rows_iterated,
            record_build_total,
            (record_build_total / rows_persisted) if rows_persisted else 0.0,
            rows_persisted,
        )

    if records:
        dn_sync_logger.info(
            (
                "Payload preparation timing: latest_merge=%.3fs, "
                "assignable_filter=%.3fs (avg %.6fs), non_null_filter=%.3fs (avg %.6fs), "
                "change_detection=%.3fs (avg %.6fs), payload_mutation=%.3fs (avg %.6fs)"
            ),
            latest_merge_total,
            assignable_filter_total,
            assignable_filter_total / len(records),
            non_null_filter_total,
            non_null_filter_total / len(records),
            change_detection_total,
            change_detection_total / len(records),
            payload_mutation_total,
            payload_mutation_total / len(records),
        )

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
        dn_sync_logger.info(
            "Applied DN changes: created=%d, updated=%d",
            created_count,
            updated_count,
        )
    else:
        dn_sync_logger.info(
            "No DN sheet changes detected; skipping database write",
        )

    normalization_start = perf_counter()
    normalize_database_fields(db)
    dn_sync_logger.debug(
        "normalize_database_fields completed in %.3fs",
        perf_counter() - normalization_start,
    )

    dn_sync_logger.info(
        (
            "Completed sync_dn_sheet_to_db run: processed_rows=%d, valid_records=%d, "
            "unique_dns=%d, skipped_missing=%d, skipped_empty=%d, updated=%d, "
            "ignored=%d, duration=%.3fs"
        ),
        total_rows,
        len(records),
        len(dn_numbers),
        skipped_missing_number,
        skipped_empty_payload,
        updated_count,
        unchanged_count,
        (datetime.utcnow() - start_time).total_seconds(),
    )

    return DnSyncResult(
        synced_numbers=sorted(dn_numbers),
        created_count=created_count,
        updated_count=updated_count,
        ignored_count=unchanged_count,
    )


def _sync_dn_sheet_with_new_session() -> DnSyncResult:
    db = SessionLocal()
    try:
        try:
            result = sync_dn_sheet_to_db(db)
        except Exception as exc:
            dn_sync_logger.exception(
                "sync_dn_sheet_to_db raised an error during manual trigger: %s", exc
            )
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
                "Synced %d DN numbers from Google Sheet (created=%d, updated=%d, ignored=%d)"
                % (
                    len(synced_numbers),
                    result.created_count,
                    result.updated_count,
                    result.ignored_count,
                )
                if synced_numbers
                else "Google Sheet returned no DN rows to sync"
            )
            create_dn_sync_log(
                db,
                status="success",
                synced_numbers=synced_numbers,
                message=message,
            )
            return result
    finally:
        db.close()


async def run_dn_sheet_sync_once() -> DnSyncResult:
    return await asyncio.to_thread(_sync_dn_sheet_with_new_session)


async def _scheduled_dn_sheet_sync() -> None:
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


@app.post("/api/dn/sync")
def trigger_dn_sync():
    try:
        result = _sync_dn_sheet_with_new_session()
    except Exception:
        logger.exception("Manual DN sheet sync failed")
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": "dn_sync_failed",
                "errorInfo": traceback.format_exc(),
            },
        )

    return {
        "ok": True,
        "synced_count": len(result.synced_numbers),
        "created_count": result.created_count,
        "updated_count": result.updated_count,
        "ignored_count": result.ignored_count,
        "dn_numbers": result.synced_numbers,
    }


@app.get("/api/dn/sync/log/latest")
def get_latest_dn_sync_log_entry(db: Session = Depends(get_db)):
    log_entry = get_latest_dn_sync_log(db)
    if not log_entry:
        return {"ok": True, "data": None}

    return {
        "ok": True,
        "data": {
            "id": log_entry.id,
            "status": log_entry.status,
            "synced_count": log_entry.synced_count,
            "dn_numbers": log_entry.dn_numbers,
            "message": log_entry.message,
            "error_message": log_entry.error_message,
            "error_traceback": log_entry.error_traceback,
            "created_at": to_gmt7_iso(log_entry.created_at),
        },
    }


@app.get("/api/dn/sync/log/file")
def download_dn_sync_log():
    if _dn_sync_file_handler is not None:
        flush = getattr(_dn_sync_file_handler, "flush", None)
        if callable(flush):
            flush()

    if not DN_SYNC_LOG_PATH.exists():
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error": "log_file_not_found"},
        )

    return FileResponse(
        path=DN_SYNC_LOG_PATH,
        filename=DN_SYNC_LOG_PATH.name,
        media_type="text/plain",
    )


# ====== Vehicle driver APIs ======


@app.post("/api/vehicle/signin")
def vehicle_signin(
    payload: VehicleSigninRequest,
    db: Session = Depends(get_db),
):
    vehicle_plate = normalize_vehicle_plate(payload.vehicle_plate)
    if not vehicle_plate:
        raise HTTPException(status_code=400, detail="vehicle_plate_required")

    lsp = (payload.lsp or "").strip()
    if not lsp:
        raise HTTPException(status_code=400, detail="lsp_required")

    arrive_time = ensure_gmt7_timezone(payload.arrive_time)

    vehicle = upsert_vehicle_signin(
        db,
        vehicle_plate=vehicle_plate,
        lsp=lsp,
        vehicle_type=payload.vehicle_type,
        driver_name=payload.driver_name,
        contact_number=payload.contact_number,
        arrive_time=arrive_time,
    )

    return {"ok": True, "vehicle": serialize_vehicle(vehicle)}


@app.get("/api/vehicle/vehicle")
def get_vehicle_info(
    vehicle_plate: str = Query(..., alias="vehiclePlate"),
    db: Session = Depends(get_db),
):
    normalized_plate = normalize_vehicle_plate(vehicle_plate)
    if not normalized_plate:
        raise HTTPException(status_code=400, detail="vehicle_plate_required")

    vehicle = get_vehicle_by_plate(db, normalized_plate)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="vehicle_not_found")

    return {"ok": True, "vehicle": serialize_vehicle(vehicle)}


@app.post("/api/vehicle/depart")
def vehicle_depart(
    payload: VehicleDepartRequest,
    db: Session = Depends(get_db),
):
    vehicle_plate = normalize_vehicle_plate(payload.vehicle_plate)
    if not vehicle_plate:
        raise HTTPException(status_code=400, detail="vehicle_plate_required")

    depart_time = ensure_gmt7_timezone(payload.depart_time)

    vehicle = mark_vehicle_departed(
        db,
        vehicle_plate=vehicle_plate,
        depart_time=depart_time,
    )

    if vehicle is None:
        raise HTTPException(status_code=404, detail="vehicle_not_found")

    return {"ok": True, "vehicle": serialize_vehicle(vehicle)}


@app.get("/api/vehicle/vehicles")
def list_vehicles_endpoint(
    status: str | None = Query(None),
    date: str | None = Query(None),
    db: Session = Depends(get_db),
):
    normalized_status: str | None = None
    if status:
        normalized_status = status.strip().lower()
        if normalized_status not in VEHICLE_VALID_STATUSES:
            raise HTTPException(status_code=400, detail="invalid_status")

    filter_by = "depart_time" if normalized_status == "departed" else "arrive_time"

    date_from = date_to = None
    if date:
        try:
            requested_date = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_date")

        start_local = datetime.combine(
            requested_date.date(),
            time(0, 0, 0, tzinfo=TZ_GMT7),
        )
        end_local = datetime.combine(
            requested_date.date(),
            time(23, 59, 59, 999999, tzinfo=TZ_GMT7),
        )
        date_from = start_local.astimezone(timezone.utc)
        date_to = end_local.astimezone(timezone.utc)

    vehicles = list_vehicle_entries(
        db,
        status=normalized_status,
        filter_by=filter_by,
        date_from=date_from,
        date_to=date_to,
    )

    return {
        "ok": True,
        "vehicles": [serialize_vehicle(vehicle) for vehicle in vehicles],
    }


@app.on_event("startup")
async def _start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _scheduled_dn_sheet_sync,
        trigger=IntervalTrigger(seconds=SHEET_SYNC_INTERVAL_SECONDS),
        id=_SHEET_SYNC_JOB_ID,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.utcnow() + timedelta(seconds=5),
    )
    _scheduler.start()


@app.on_event("shutdown")
async def _shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


@app.get("/api/dn/stats/{date}")
async def get_dn_stats(date: str):
    # 初始化Google Sheets客户端
    gc = create_gspread_client()
    sh = gc.open_by_url(SPREADSHEET_URL)

    # 获取所有工作表数据
    combined_df = process_all_sheets(sh)

    # 处理日期列
    combined_df["plan_mos_date"] = combined_df["plan_mos_date"].apply(
        lambda x: parse_date(x).strftime("%d-%b-%y") if parse_date(x) else x
    )

    # 筛选出特定日期的数据
    day_df = combined_df[combined_df["plan_mos_date"] == date]
    day_df["status_delivery"] = day_df["status_delivery"].apply(lambda x: x.upper() if x else "NO STATUS")

    # 创建透视表
    pivot_df = day_df.groupby(["plan_mos_date", "region", "status_delivery"])["dn_number"].nunique().unstack(fill_value=0)

    # 所有可能的状态值
    all_statuses = [
        "PREPARE VEHICLE", "ON THE WAY", "ON SITE", "POD", "REPLAN MOS PROJECT", "WAITING PIC FEEDBACK", 
        "REPLAN MOS DUE TO LSP DELAY", "CLOSE BY RN", "CANCEL MOS", "NO STATUS"
    ]

    # 补充状态值
    extra = list(set(pivot_df.columns.tolist()) - set(all_statuses))
    final_statuses = all_statuses + extra

    # 重新索引并添加总计列
    pivot_df = pivot_df.reindex(columns=final_statuses, fill_value=0)
    pivot_df["Total"] = pivot_df.sum(axis=1)

    # 转换为最终表格格式
    table_df = pivot_df.reset_index()
    table_df.columns = ["date", "group"] + table_df.columns.to_list()[2:]

    # 转换为所需的格式
    raw_rows = [
        {
            'group': row['group'],
            'date': row['date'],
            'values': list(row)[2:]
        }
        for _, row in table_df.iterrows()
    ]

    return {"ok": True, "data": raw_rows}


@app.get("/api/dn/filters")
def get_dn_filter_options(db: Session = Depends(get_db)):
    values, total = get_dn_unique_field_values(db)

    data: dict[str, Any] = {**values, "total": total}
    if "status_delivery" in data:
        data.setdefault("status_deliver", data["status_delivery"])

    return {"ok": True, "data": data}


@app.get("/api/dn/status-delivery/stats")
def get_dn_status_delivery_stats(
    lsp: Optional[str] = Query(default=None),
    plan_mos_date: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    normalized_lsp = lsp.strip() if lsp else None

    normalized_plan_mos_date = plan_mos_date.strip() if plan_mos_date else None
    if not normalized_plan_mos_date:
        normalized_plan_mos_date = datetime.now().strftime("%d %b %y")

    stats = get_dn_status_delivery_counts(
        db,
        lsp=normalized_lsp,
        plan_mos_date=normalized_plan_mos_date,
    )
    total = sum(count for _, count in stats)

    data = [
        {"status_delivery": status, "count": count}
        for status, count in stats
    ]

    return {"ok": True, "data": data, "total": total}


@app.get("/api/dn/list")
async def get_dn_list(db: Session = Depends(get_db)):
    items = db.query(DN).order_by(DN.dn_number.asc()).all()

    if not items:
        return {"ok": True, "data": []}

    latest_records = get_latest_dn_records_map(db, [it.dn_number for it in items])
    sheet_columns = get_sheet_columns()

    data: List[dict[str, Any]] = []
    for it in items:
        row: dict[str, Any] = {
            "id": it.id,
            "dn_number": it.dn_number,
            "created_at": to_gmt7_iso(it.created_at),
            "status": it.status,
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "last_updated_by": it.last_updated_by,
            "gs_sheet": it.gs_sheet,
            "gs_row": it.gs_row,
        }
        for column in sheet_columns:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = to_gmt7_iso(
            latest.created_at if latest else None
        )
        data.append(row)

    return {"ok": True, "data": data}


@app.get("/api/dn/list/search")
def search_dn_list_api(
    date: Optional[List[str]] = Query(None, description="Plan MOS date"),
    dn_number: str | None = Query(None, description="DN number"),
    dnnumber_legacy: str | None = Query(
        None,
        alias="dnnumber",
        description="DN number (legacy alias)",
        include_in_schema=False,
    ),
    du_id: str | None = Query(None, description="关联 DU ID"),
    du_id_legacy: str | None = Query(
        None,
        alias="duId",
        description="关联 DU ID (legacy alias)",
        include_in_schema=False,
    ),
    status_delivery: Optional[List[str]] = Query(None, description="Status delivery"),
    status_delivery_legacy: Optional[List[str]] = Query(
        None,
        alias="statusDelivery",
        description="Status delivery (legacy alias)",
        include_in_schema=False,
    ),
    status_values_param: Optional[List[str]] = Query(
        None,
        alias="status",
        description="Status",
    ),
    status_not_empty: bool | None = Query(
        None,
        description="仅返回状态不为空的 DN 记录",
    ),
    has_coordinate: bool | None = Query(
        None,
        description="根据是否存在经纬度筛选 DN 记录",
    ),
    lsp: Optional[List[str]] = Query(None, description="LSP"),
    region: Optional[List[str]] = Query(None, description="Region"),
    area: str | None = Query(None, description="Area"),
    status_wh: Optional[List[str]] = Query(None, description="Status WH"),
    subcon: Optional[List[str]] = Query(None, description="Subcon"),
    project: str | None = Query(None, description="Project request"),
    date_from: datetime | None = Query(
        None, description="Last modified start time (ISO 8601)"
    ),
    date_to: datetime | None = Query(
        None, description="Last modified end time (ISO 8601)"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    dn_query_value = dn_number or dnnumber_legacy
    dn_number = normalize_dn(dn_query_value) if dn_query_value else None
    if dn_number and not DN_RE.fullmatch(dn_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")
    du_query_value = du_id or du_id_legacy
    du_id = normalize_du(du_query_value) if du_query_value else None
    if du_id and not DU_RE.fullmatch(du_id):
        raise HTTPException(status_code=400, detail=f"Invalid DU ID: {du_id}")

    plan_mos_dates = _collect_query_values(date)
    status_delivery_values = _collect_query_values(
        status_delivery, status_delivery_legacy
    )
    status_values = _collect_query_values(status_values_param)
    lsp_values = _collect_query_values(lsp)
    region_values = _collect_query_values(region)
    status_wh_values = _collect_query_values(status_wh)
    subcon_values = _collect_query_values(subcon)
    area_value = area.strip() if area else None
    project_value = project.strip() if project else None
    modified_from, modified_to = parse_gmt7_date_range(date_from, date_to)

    total, items = search_dn_list(
        db,
        plan_mos_dates=plan_mos_dates,
        dn_number=dn_number,
        du_id=du_id,
        status_delivery_values=status_delivery_values,
        status_values=status_values,
        status_not_empty=status_not_empty,
        has_coordinate=has_coordinate,
        lsp_values=lsp_values,
        region_values=region_values,
        area=area_value,
        status_wh_values=status_wh_values,
        subcon_values=subcon_values,
        project_request=project_value,
        last_modified_from=modified_from,
        last_modified_to=modified_to,
        page=page,
        page_size=page_size,
    )

    if not items:
        return {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [],
        }

    latest_records = get_latest_dn_records_map(db, [it.dn_number for it in items])
    sheet_columns = get_sheet_columns()

    data: list[dict[str, Any]] = []
    for it in items:
        row: dict[str, Any] = {
            "id": it.id,
            "dn_number": it.dn_number,
            "created_at": to_gmt7_iso(it.created_at),
            "status": it.status,
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "last_updated_by": it.last_updated_by,
            "gs_sheet": it.gs_sheet,
            "gs_row": it.gs_row,
        }
        for column in sheet_columns:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = to_gmt7_iso(
            latest.created_at if latest else None
        )
        data.append(row)

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": data,
    }


@app.get("/api/dn/records")
def get_all_dn_records(db: Session = Depends(get_db)):
    items = list_all_dn_records(db)
    return {
        "ok": True,
        "total": len(items),
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
                "status": it.status,
                "remark": it.remark,
                "photo_url": it.photo_url,
                "lng": it.lng,
                "lat": it.lat,
                "updated_by": it.updated_by,
                "created_at": to_gmt7_iso(it.created_at),
            }
            for it in items
        ],
    }


@app.get("/api/dn/list/batch")
def batch_search_dn_list(
    dn_number: Optional[List[str]] = Query(None, description="重复 dn_number 或逗号分隔"),
    dnnumber_legacy: Optional[List[str]] = Query(
        None,
        alias="dnnumber",
        description="重复 dn_number 或逗号分隔 (legacy alias)",
        include_in_schema=False,
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    dn_numbers = _normalize_batch_dn_numbers(dn_number, dnnumber_legacy)

    total, items = list_dn_by_dn_numbers(
        db, dn_numbers, page=page, page_size=page_size
    )

    if not items:
        return {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [],
        }

    latest_records = get_latest_dn_records_map(db, [it.dn_number for it in items])
    sheet_columns = get_sheet_columns()

    data: list[dict[str, Any]] = []
    for it in items:
        row: dict[str, Any] = {
            "id": it.id,
            "dn_number": it.dn_number,
            "created_at": to_gmt7_iso(it.created_at),
            "status": it.status,
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "last_updated_by": it.last_updated_by,
            "gs_sheet": it.gs_sheet,
            "gs_row": it.gs_row,
        }
        for column in sheet_columns:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = to_gmt7_iso(
            latest.created_at if latest else None
        )
        data.append(row)

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": data,
    }


@app.delete("/api/dn/{dn_number}")
def remove_dn(dn_number: str, db: Session = Depends(get_db)):
    normalized_number = normalize_dn(dn_number)
    if not DN_RE.fullmatch(normalized_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    ok = delete_dn(db, normalized_number)
    if not ok:
        raise HTTPException(status_code=404, detail="DN not found")

    return {"ok": True}


@app.get("/api/dn/{dn_number}")
def get_dn_records(dn_number: str, db: Session = Depends(get_db)):
    dn_number = normalize_dn(dn_number)
    if not DN_RE.fullmatch(dn_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    items = list_dn_records(db, dn_number)
    return {
        "ok": True,
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
                "status": it.status,
                "remark": it.remark,
                "photo_url": it.photo_url,
                "lng": it.lng,
                "lat": it.lat,
                "updated_by": it.updated_by,
                "created_at": to_gmt7_iso(it.created_at),
            }
            for it in items
        ],
    }


# 可选：支持 python -m app.main 本地跑，避免相对导入报错
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=10000, reload=True)
