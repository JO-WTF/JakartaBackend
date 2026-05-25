# crud.py
from __future__ import annotations

import json
from typing import Any, Optional, Iterable, Tuple, List, Set, Dict, Sequence, Literal
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import and_, func, or_, case, exists
from .models import DN, DNRecord, DNSyncLog, Vehicle, StatusDeliveryLspStat, PM, PMInventory
import unicodedata
from .dn_columns import (
    filter_assignable_dn_fields,
    ensure_dynamic_columns_loaded,
    get_mutable_dn_columns,
)

_ACTIVE_DN_EXPR = func.coalesce(DN.is_deleted, "N") == "N"


def _normalize_vehicle_plate(vehicle_plate: str) -> str:
    return "".join(vehicle_plate.split()).upper()


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def upsert_vehicle_signin(
    db: Session,
    *,
    vehicle_plate: str,
    lsp: str,
    vehicle_type: str | None = None,
    driver_name: str | None = None,
    contact_number: str | None = None,
    arrive_time: datetime | None = None,
) -> Vehicle:
    plate = _normalize_vehicle_plate(vehicle_plate)
    if not plate:
        raise ValueError("vehicle_plate is required")

    arrive_time = _normalize_timestamp(arrive_time) or datetime.now(timezone.utc)

    vehicle = db.query(Vehicle).filter(func.upper(Vehicle.vehicle_plate) == plate).one_or_none()

    if vehicle is None:
        vehicle = Vehicle(vehicle_plate=plate, lsp=lsp)

    vehicle.vehicle_type = vehicle_type
    vehicle.driver_name = driver_name
    vehicle.contact_number = contact_number
    vehicle.lsp = lsp
    vehicle.arrive_time = arrive_time
    vehicle.depart_time = None
    vehicle.status = "arrived"

    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


def get_vehicle_by_plate(db: Session, vehicle_plate: str) -> Vehicle | None:
    plate = _normalize_vehicle_plate(vehicle_plate)
    if not plate:
        return None

    return db.query(Vehicle).filter(func.upper(Vehicle.vehicle_plate) == plate).one_or_none()


def mark_vehicle_departed(
    db: Session,
    *,
    vehicle_plate: str,
    depart_time: datetime | None = None,
) -> Vehicle | None:
    vehicle = get_vehicle_by_plate(db, vehicle_plate)
    if vehicle is None:
        return None

    depart_time = _normalize_timestamp(depart_time) or datetime.now(timezone.utc)

    vehicle.depart_time = depart_time
    vehicle.status = "departed"

    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


def list_vehicles(
    db: Session,
    *,
    status: str | None = None,
    filter_by: Literal["arrive_time", "depart_time"] = "arrive_time",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> List[Vehicle]:
    query = db.query(Vehicle)

    if status:
        query = query.filter(Vehicle.status == status)

    if date_from is not None or date_to is not None:
        column = Vehicle.depart_time if filter_by == "depart_time" else Vehicle.arrive_time
        if filter_by == "depart_time":
            query = query.filter(Vehicle.depart_time.isnot(None))
        else:
            query = query.filter(Vehicle.arrive_time.isnot(None))

        if date_from is not None:
            query = query.filter(column >= date_from)
        if date_to is not None:
            query = query.filter(column <= date_to)

    return query.order_by(Vehicle.arrive_time.desc(), Vehicle.id.desc()).all()


def ensure_dn(db: Session, dn_number: str, **fields: Any) -> DN:
    ensure_dynamic_columns_loaded(db)
    allowed_columns = get_mutable_dn_columns(db)
    assignable = filter_assignable_dn_fields(fields, allowed_columns=allowed_columns)
    # Exclude is_deleted from non_null_assignable to avoid conflicts
    # since we explicitly set it in the constructor
    non_null_assignable = {k: v for k, v in assignable.items() if v is not None and k != "is_deleted"}

    dn = db.query(DN).filter(DN.dn_number == dn_number).one_or_none()
    if not dn:
        dn = DN(dn_number=dn_number, is_deleted="N", **non_null_assignable)
        db.add(dn)
        db.commit()
        db.refresh(dn)
        return dn

    updated = False
    if dn.is_deleted != "N":
        dn.is_deleted = "N"
        updated = True
    for key, value in non_null_assignable.items():
        if getattr(dn, key, None) != value:
            setattr(dn, key, value)
            updated = True

    # Allow explicit updates to nullable fields (e.g. last_updated_by) when
    # they are provided in the payload even if the value is None.
    for key, value in assignable.items():
        if key in non_null_assignable:
            continue
        if getattr(dn, key, None) is not None and value is None:
            setattr(dn, key, None)
            updated = True

    if updated:
        db.add(dn)
        db.commit()
        db.refresh(dn)

    return dn


def _serialize_dn_record(record: DNRecord) -> Dict[str, Any]:
    return {
        "id": record.id,
        "dn_number": record.dn_number,
        "status_delivery": record.status_delivery,
        "status_site": record.status_site,
        "remark": record.remark,
        "photo_url": record.photo_url,
        "lng": record.lng,
        "lat": record.lat,
        "updated_by": record.updated_by,
        "phone_number": record.phone_number,
        "created_at": record.created_at,
    }


def _serialize_dn(dn: DN) -> Dict[str, Any]:
    return {
        "id": dn.id,
        "dn_number": dn.dn_number,
        "status_delivery": dn.status_delivery,
        "status_site": dn.status_site,
        "remark": dn.remark,
        "photo_url": dn.photo_url,
        "lng": dn.lng,
        "lat": dn.lat,
        "driver_contact_number": dn.driver_contact_number,
        "last_updated_by": dn.last_updated_by,
        "gs_sheet": dn.gs_sheet,
        "gs_row": dn.gs_row,
        "update_count": dn.update_count,
    }


def delete_dn(db: Session, dn_number: str) -> Dict[str, Any] | None:
    dn = db.query(DN).filter(DN.dn_number == dn_number).one_or_none()
    if not dn:
        return None

    dn_data = _serialize_dn(dn)
    related_records = db.query(DNRecord).filter(DNRecord.dn_number == dn_number).all()
    related_records_data = [_serialize_dn_record(record) for record in related_records]

    db.query(DNRecord).filter(DNRecord.dn_number == dn_number).delete(synchronize_session=False)
    db.delete(dn)
    db.commit()
    return {"dn": dn_data, "records": related_records_data}


def add_dn_record(
    db: Session,
    dn_number: str,
    status_delivery: str | None = None,
    status_site: str | None = None,
    remark: str | None = None,
    photo_url: str | None = None,
    lng: str | None = None,
    lat: str | None = None,
    updated_by: str | None = None,
    phone_number: str | None = None,
) -> DNRecord:
    rec = DNRecord(
        dn_number=dn_number,
        remark=remark,
        photo_url=photo_url,
        lng=lng,
        lat=lat,
        updated_by=updated_by,
        phone_number=phone_number,
        status_delivery=status_delivery,
        status_site=status_site,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    # Keep the DN table in sync with the latest record that was just created.
    ensure_payload: dict[str, Any] = {
        "remark": remark,
        "photo_url": photo_url,
        "lng": lng,
        "lat": lat,
    }
    if status_delivery is not None:
        ensure_payload["status_delivery"] = status_delivery
    if status_site is not None:
        ensure_payload["status_site"] = status_site
    if updated_by is not None:
        ensure_payload["last_updated_by"] = updated_by
    if phone_number is not None:
        ensure_payload["driver_contact_number"] = phone_number

    # Increment update_count
    dn = ensure_dn(
        db,
        dn_number,
        **ensure_payload,
    )
    dn.update_count = (dn.update_count or 0) + 1
    db.add(dn)
    db.commit()
    db.refresh(rec)
    return rec


def create_dn_sync_log(
    db: Session,
    *,
    status: str,
    synced_numbers: Iterable[str] | None = None,
    message: Optional[str] = None,
    error_message: Optional[str] = None,
    error_traceback: Optional[str] = None,
) -> DNSyncLog:
    numbers_list = sorted({str(num) for num in (synced_numbers or []) if str(num)})
    log = DNSyncLog(
        status=status,
        synced_count=len(numbers_list),
        dn_numbers_json=json.dumps(numbers_list) if numbers_list else None,
        message=message,
        error_message=error_message,
        error_traceback=error_traceback,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def get_latest_dn_sync_log(db: Session) -> Optional[DNSyncLog]:
    return db.query(DNSyncLog).order_by(DNSyncLog.created_at.desc(), DNSyncLog.id.desc()).first()


def list_dn_records(db: Session, dn_number: str, limit: int = 50) -> List[DNRecord]:
    q = db.query(DNRecord).filter(DNRecord.dn_number == dn_number).order_by(DNRecord.created_at.desc()).limit(limit)
    return q.all()


def list_all_dn_records(db: Session) -> List[Tuple[DNRecord, DN]]:
    query = db.query(DNRecord, DN) \
        .join(DN, DNRecord.dn_number == DN.dn_number, isouter=True) \
        .order_by(DNRecord.created_at.desc(), DNRecord.id.desc())
    # 执行查询，获取所有结果
    return query.all()


def search_dn_records(
    db: Session,
    *,
    dn_number: Optional[str] = None,
    du_id: Optional[str] = None,
    status_delivery: Optional[str] = None,
    status_site: Optional[str] = None,
    remark_keyword: Optional[str] = None,
    phone_number: Optional[str] = None,
    has_photo: Optional[bool] = None,
    date_from=None,
    date_to=None,
    page: int = 1,
    page_size: Optional[int] = None,
) -> Tuple[int, List[DNRecord]]:
    base_q = db.query(DNRecord)
    conds = []
    if dn_number:
        conds.append(DNRecord.dn_number == dn_number)
    if du_id:
        conds.append(DNRecord.du_id == du_id)
    if status_delivery:
        conds.append(DNRecord.status_delivery == status_delivery)
    if status_site:
        conds.append(DNRecord.status_site.ilike(f"%{status_site}%"))
    if remark_keyword:
        conds.append(DNRecord.remark.ilike(f"%{remark_keyword}%"))
    if isinstance(phone_number, str):
        trimmed_phone = phone_number.strip()
        if trimmed_phone:
            conds.append(func.trim(DNRecord.phone_number) == trimmed_phone)
    if has_photo is True:
        conds.append(DNRecord.photo_url.isnot(None))
    elif has_photo is False:
        conds.append(DNRecord.photo_url.is_(None))
    if date_from is not None:
        conds.append(DNRecord.created_at >= date_from)
    if date_to is not None:
        conds.append(DNRecord.created_at <= date_to)
    if conds:
        base_q = base_q.filter(and_(*conds))

    total = base_q.count()
    ordered_q = base_q.order_by(DNRecord.created_at.desc(), DNRecord.id.desc())
    if page_size is None:
        items = ordered_q.all()
    else:
        items = ordered_q.offset((page - 1) * page_size).limit(page_size).all()
    return total, items


def get_dn_record_by_id(db: Session, rec_id: int) -> Optional[DNRecord]:
    return db.query(DNRecord).get(rec_id)


def update_dn_record(
    db: Session,
    rec_id: int,
    *,
    status_delivery: Optional[str] = None,
    status_site: Optional[str] = None,
    remark: Optional[str] = None,
    photo_url: Optional[str] = None,
    updated_by: Optional[str] = None,
    updated_by_set: bool = False,
    phone_number: Optional[str] = None,
    phone_number_set: bool = False,
) -> Optional[DNRecord]:
    obj = db.query(DNRecord).get(rec_id)
    if not obj:
        return None

    if status_delivery is not None:
        obj.status_delivery = status_delivery
    if status_site is not None:
        obj.status_site = status_site
    if remark is not None:
        obj.remark = remark
    if photo_url is not None:
        obj.photo_url = photo_url
    if updated_by_set:
        obj.updated_by = updated_by
    elif updated_by is not None:
        obj.updated_by = updated_by
    if phone_number_set:
        obj.phone_number = phone_number
    elif phone_number is not None:
        obj.phone_number = phone_number

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete_dn_record(db: Session, rec_id: int) -> Dict[str, Any] | None:
    obj = db.query(DNRecord).get(rec_id)
    if not obj:
        return None
    record_data = _serialize_dn_record(obj)
    db.delete(obj)
    db.commit()
    return record_data


def list_dn_records_by_dn_numbers(
    db: Session,
    dn_numbers: Iterable[str],
    *,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[int, List[DNRecord]]:
    dn_numbers = [x for x in {x for x in dn_numbers if x}]
    if not dn_numbers:
        return 0, []

    base_q = db.query(DNRecord).filter(DNRecord.dn_number.in_(dn_numbers))

    total = base_q.count()
    items = (
        base_q.order_by(DNRecord.created_at.desc(), DNRecord.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return total, items


def list_dn_by_dn_numbers(
    db: Session,
    dn_numbers: Iterable[str],
    *,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[int, List[DN]]:
    numbers = [number for number in dict.fromkeys(dn_numbers) if number]
    if not numbers:
        return 0, []

    latest_record_subq = (
        db.query(
            DNRecord.dn_number.label("dn_number"),
            func.max(DNRecord.created_at).label("latest_record_created_at"),
        )
        .group_by(DNRecord.dn_number)
        .subquery()
    )

    base_q = (
        db.query(DN)
        .outerjoin(latest_record_subq, DN.dn_number == latest_record_subq.c.dn_number)
        .filter(DN.dn_number.in_(numbers))
    )

    total = base_q.count()

    latest_record_expr = func.coalesce(latest_record_subq.c.latest_record_created_at, DN.created_at)

    items = (
        base_q.order_by(latest_record_expr.desc(), DN.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    )
    return total, items


def list_dn_by_du_ids(
    db: Session,
    du_ids: Iterable[str],
    *,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[int, List[DN]]:
    identifiers = [value for value in dict.fromkeys(du_ids) if value]
    if not identifiers:
        return 0, []

    latest_record_subq = (
        db.query(
            DNRecord.dn_number.label("dn_number"),
            func.max(DNRecord.created_at).label("latest_record_created_at"),
        )
        .group_by(DNRecord.dn_number)
        .subquery()
    )

    base_q = (
        db.query(DN)
        .outerjoin(latest_record_subq, DN.dn_number == latest_record_subq.c.dn_number)
        .filter(DN.du_id.in_(identifiers))
    )

    total = base_q.count()

    latest_record_expr = func.coalesce(latest_record_subq.c.latest_record_created_at, DN.created_at)

    items = (
        base_q.order_by(latest_record_expr.desc(), DN.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    )
    return total, items


# PM / PMInventory helpers


def create_pm(
    db: Session,
    pm_name: str,
    lng: str | None = None,
    lat: str | None = None,
    address: str | None = None,
) -> PM:
    """Create or return existing PM by case-insensitive name."""
    if not pm_name or not isinstance(pm_name, str):
        raise ValueError("pm_name is required")
    # Normalize unicode and trim whitespace to avoid mismatches
    name = unicodedata.normalize("NFC", pm_name).strip()
    if not name:
        raise ValueError("pm_name is required")

    name_lower = name.lower()
    existing = db.query(PM).filter(func.lower(PM.pm_name) == name_lower).one_or_none()
    if existing:
        return existing

    pm = PM(pm_name=name, lng=lng, lat=lat, address=address)
    db.add(pm)
    db.commit()
    db.refresh(pm)
    return pm


def pm_inbound(db: Session, pm_name: str, dn_number: str) -> PMInventory:
    """Register a DN as inbound to a PM. Raises ValueError on errors."""
    if not pm_name or not isinstance(pm_name, str):
        raise ValueError("pm required")
    if not dn_number or not isinstance(dn_number, str):
        raise ValueError("invalid dn_number")

    name = unicodedata.normalize("NFC", pm_name).strip()
    dn = dn_number.strip()

    pm_obj = db.query(PM).filter(func.lower(PM.pm_name) == name.lower()).one_or_none()
    if not pm_obj:
        raise ValueError("pm not found")

    existing = (
        db.query(PMInventory)
        .filter(PMInventory.dn_number == dn)
        .filter(func.coalesce(PMInventory.status, "") != "out")
        .order_by(PMInventory.in_time.desc())
        .first()
    )
    if existing:
        raise ValueError("dn already in inventory")

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    rec = PMInventory(pm_name=pm_obj.pm_name, dn_number=dn, status="in", in_time=now)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def pm_outbound(db: Session, pm_name: str, dn_number: str) -> PMInventory:
    """Mark a DN as outbound from a PM. Raises ValueError if not found."""
    if not pm_name or not isinstance(pm_name, str):
        raise ValueError("pm required")
    if not dn_number or not isinstance(dn_number, str):
        raise ValueError("invalid dn_number")

    name = unicodedata.normalize("NFC", pm_name).strip()
    dn = dn_number.strip()

    rec = (
        db.query(PMInventory)
        .filter(func.lower(PMInventory.pm_name) == name.lower())
        .filter(PMInventory.dn_number == dn)
        .filter(func.coalesce(PMInventory.status, "") != "out")
        .order_by(PMInventory.in_time.desc())
        .first()
    )
    if not rec:
        raise ValueError("inventory record not found")

    from datetime import datetime, timezone

    rec.status = "out"
    rec.out_time = datetime.now(timezone.utc)
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def find_pm_by_dn(db: Session, dn_number: str) -> PMInventory | None:
    """Return latest PMInventory record for this DN that is not out, or None."""
    if not dn_number or not isinstance(dn_number, str):
        return None
    dn = dn_number.strip()
    rec = (
        db.query(PMInventory)
        .filter(PMInventory.dn_number == dn)
        .filter(func.coalesce(PMInventory.status, "") != "out")
        .order_by(PMInventory.in_time.desc())
        .first()
    )
    return rec


def list_pm_inventory(db: Session, pm_name: str) -> list[PMInventory]:
    """Return all PMInventory records for pm_name with status != 'out'."""
    if not pm_name or not isinstance(pm_name, str):
        return []
    name = unicodedata.normalize("NFC", pm_name).strip()
    records = (
        db.query(PMInventory)
        .filter(func.lower(PMInventory.pm_name) == name.lower())
        .filter(func.coalesce(PMInventory.status, "") != "out")
        .order_by(PMInventory.in_time.desc())
        .all()
    )
    return records


def delete_pm(db: Session, pm_name: str) -> bool:
    """Delete a PM by case-insensitive name. Returns True when a row was removed."""
    if not pm_name or not isinstance(pm_name, str):
        raise ValueError("pm_name is required")
    name = unicodedata.normalize("NFC", pm_name).strip()
    if not name:
        raise ValueError("pm_name is required")

    pm_row = db.query(PM).filter(func.lower(PM.pm_name) == name.lower()).one_or_none()
    if pm_row is None:
        return False

    db.delete(pm_row)
    db.commit()
    return True


def get_existing_dn_numbers(db: Session, dn_numbers: Iterable[str]) -> Set[str]:
    unique_numbers = {dn_number for dn_number in dn_numbers if dn_number}
    if not unique_numbers:
        return set()

    rows = db.query(DN.dn_number).filter(DN.dn_number.in_(unique_numbers)).all()
    return {row[0] for row in rows}


def get_dn_map_by_numbers(db: Session, dn_numbers: Iterable[str]) -> Dict[str, DN]:
    """Return a mapping of dn_number to DN rows for the provided identifiers."""

    numbers = [number for number in {number for number in dn_numbers if number}]
    if not numbers:
        return {}

    rows = db.query(DN).filter(DN.dn_number.in_(numbers)).order_by(DN.dn_number.asc()).all()

    return {row.dn_number: row for row in rows}


def get_latest_dn_records_map(db: Session, dn_numbers: Iterable[str]) -> Dict[str, DNRecord]:
    unique_numbers = [number for number in {number for number in dn_numbers if number}]
    if not unique_numbers:
        return {}

    q = (
        db.query(DNRecord)
        .filter(DNRecord.dn_number.in_(unique_numbers))
        .order_by(DNRecord.dn_number.asc(), DNRecord.created_at.desc(), DNRecord.id.desc())
    )

    latest: Dict[str, DNRecord] = {}
    for rec in q:
        key = rec.dn_number
        if key not in latest:
            latest[key] = rec
            if len(latest) == len(unique_numbers):
                break
    return latest


def search_dn_list(
    db: Session,
    *,
    plan_mos_dates: Sequence[str] | None = None,
    dn_numbers: Sequence[str] | None = None,
    du_id: str | None = None,
    phone_number: str | None = None,
    status_delivery_values: Sequence[str] | None = None,
    status_site_values: Sequence[str] | None = None,
    status_delivery_not_empty: bool | None = None,
    status_site_not_empty: bool | None = None,
    # status_not_empty 已废弃
    has_coordinate: bool | None = None,
    lsp_values: Sequence[str] | None = None,
    region_values: Sequence[str] | None = None,
    area: Sequence[str] | None = None,
    status_wh_values: Sequence[str] | None = None,
    subcon_values: Sequence[str] | None = None,
    project_request: Sequence[str] | None = None,
    mos_type_values: Sequence[str] | None = None,
    last_modified_from: datetime | None = None,
    last_modified_to: datetime | None = None,
    show_deleted: bool = False,
    page: int = 1,
    page_size: int | None = 20,
) -> Tuple[int, List[DN]]:
    latest_record_subq = (
        db.query(
            DNRecord.dn_number.label("dn_number"),
            func.max(DNRecord.created_at).label("latest_record_created_at"),
        )
        .group_by(DNRecord.dn_number)
        .subquery()
    )

    base_q = db.query(DN).outerjoin(latest_record_subq, DN.dn_number == latest_record_subq.c.dn_number)

    # Apply deleted filter based on show_deleted parameter
    if not show_deleted:
        base_q = base_q.filter(_ACTIVE_DN_EXPR)
    latest_record_expr = func.coalesce(latest_record_subq.c.latest_record_created_at, DN.created_at)
    last_modified_expr = func.greatest(DN.created_at, latest_record_expr)
    conds = []

    trimmed_plan_mos_dates = [
        value.strip() for value in (plan_mos_dates or []) if isinstance(value, str) and value.strip()
    ]
    if trimmed_plan_mos_dates:
        conds.append(func.trim(DN.plan_mos_date).in_(trimmed_plan_mos_dates))
    trimmed_status_site_values = [
        value.strip() for value in (status_site_values or []) if isinstance(value, str) and value.strip()
    ]
    if trimmed_status_site_values:
        conds.append(DN.status_site.in_(trimmed_status_site_values))
    if dn_numbers:
        conds.append(DN.dn_number.in_(dn_numbers))
    if du_id:
        conds.append(DN.du_id == du_id)
    trimmed_phone_number = phone_number.strip() if isinstance(phone_number, str) else None
    if trimmed_phone_number:
        phone_match_exists = (
            exists()
            .where(
                and_(
                    DNRecord.dn_number == DN.dn_number,
                    func.trim(DNRecord.phone_number) == trimmed_phone_number,
                )
            )
            .correlate(DN)
        )
        conds.append(
            or_(
                func.trim(DN.driver_contact_number) == trimmed_phone_number,
                phone_match_exists,
            )
        )
    normalized_status_delivery = [
        value.strip().lower() for value in (status_delivery_values or []) if isinstance(value, str) and value.strip()
    ]
    if normalized_status_delivery:
        conds.append(func.lower(func.trim(DN.status_delivery)).in_(normalized_status_delivery))

    if status_delivery_not_empty is True:
        conds.append(
            and_(
                DN.status_delivery.isnot(None),
                func.length(func.trim(DN.status_delivery)) > 0,
                func.lower(func.trim(DN.status_delivery)) != "no status",
            )
        )
    elif status_delivery_not_empty is False:
        conds.append(
            or_(
                DN.status_delivery.is_(None),
                func.length(func.trim(DN.status_delivery)) == 0,
                func.lower(func.trim(DN.status_delivery)) != "no status",
            )
        )

    if status_site_not_empty is True:
        conds.append(
            and_(
                DN.status_site.isnot(None),
                func.length(func.trim(DN.status_site)) > 0,
            )
        )
    elif status_site_not_empty is False:
        conds.append(
            or_(
                DN.status_site.is_(None),
                func.length(func.trim(DN.status_site)) == 0,
            )
        )
    if has_coordinate is True:
        conds.append(
            and_(
                DN.lat.isnot(None),
                func.length(func.trim(DN.lat)) > 0,
                DN.lng.isnot(None),
                func.length(func.trim(DN.lng)) > 0,
            )
        )
    elif has_coordinate is False:
        conds.append(
            or_(
                DN.lat.is_(None),
                DN.lng.is_(None),
                func.length(func.trim(DN.lat)) == 0,
                func.length(func.trim(DN.lng)) == 0,
            )
        )
    trimmed_lsp_values = [value.strip() for value in (lsp_values or []) if isinstance(value, str) and value.strip()]
    if trimmed_lsp_values:
        conds.append(func.trim(DN.lsp).in_(trimmed_lsp_values))
    trimmed_region_values = [
        value.strip() for value in (region_values or []) if isinstance(value, str) and value.strip()
    ]
    if trimmed_region_values:
        conds.append(func.trim(DN.region).in_(trimmed_region_values))
    trimmed_area_values = [value.strip() for value in (area or []) if isinstance(value, str) and value.strip()]
    if trimmed_area_values:
        conds.append(func.trim(DN.area).in_(trimmed_area_values))
    trimmed_status_wh_values = [
        value.strip() for value in (status_wh_values or []) if isinstance(value, str) and value.strip()
    ]
    if trimmed_status_wh_values:
        conds.append(func.trim(DN.status_wh).in_(trimmed_status_wh_values))
    trimmed_subcon_values = [
        value.strip() for value in (subcon_values or []) if isinstance(value, str) and value.strip()
    ]
    if trimmed_subcon_values:
        conds.append(func.trim(DN.subcon).in_(trimmed_subcon_values))
    trimmed_mos_type_values = [
        value.strip() for value in (mos_type_values or []) if isinstance(value, str) and value.strip()
    ]
    if trimmed_mos_type_values:
        conds.append(func.trim(DN.mos_type).in_(trimmed_mos_type_values))
    trimmed_project_requests = [
        value.strip() for value in (project_request or []) if isinstance(value, str) and value.strip()
    ]
    if trimmed_project_requests:
        conds.append(func.trim(DN.project_request).in_(trimmed_project_requests))

    if last_modified_from is not None:
        conds.append(last_modified_expr >= last_modified_from)
    if last_modified_to is not None:
        conds.append(last_modified_expr <= last_modified_to)

    if conds:
        base_q = base_q.filter(and_(*conds))

    total = base_q.count()
    ordered_q = base_q.order_by(latest_record_expr.desc(), DN.id.desc())

    if page_size is None:
        items = ordered_q.all()
    else:
        items = ordered_q.offset((page - 1) * page_size).limit(page_size).all()
    return total, items


def get_dn_unique_field_values(db: Session) -> Tuple[Dict[str, List[str]], int]:
    """Return unique DN field values for filter options along with total count."""

    columns: Dict[str, Any] = {
        "lsp": DN.lsp,
        "area": DN.area,
        "region": DN.region,
        "plan_mos_date": DN.plan_mos_date,
        "mos_type": DN.mos_type,
        "subcon": DN.subcon,
        "status_wh": DN.status_wh,
        "status_delivery": DN.status_delivery,
        "status_site": DN.status_site,
        "project_request": DN.project_request,
    }

    distinct_values: Dict[str, List[str]] = {}

    for key, column in columns.items():
        trimmed = func.trim(column).label("value")
        query = (
            db.query(trimmed)
            .filter(_ACTIVE_DN_EXPR)
            .filter(column.isnot(None))
            .filter(func.length(trimmed) > 0)
            .distinct()
            .order_by(trimmed.asc())
        )
        values = [row.value for row in query.all() if row.value]

        if key == "plan_mos_date":
            values = _sort_plan_mos_dates_desc(values)

        distinct_values[key] = values

    total = db.query(func.count(DN.id)).filter(_ACTIVE_DN_EXPR).scalar() or 0

    return distinct_values, int(total)


def _sort_plan_mos_dates_desc(values: List[str]) -> List[str]:
    """Sort plan_mos_date values descending by parsed date when possible."""

    def _parse(value: str) -> datetime | None:
        formats = [
            "%d %b %y",
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%Y/%m/%d",
            "%d/%m/%Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None

    return sorted(
        values,
        key=lambda v: (_parse(v) or datetime.min, v),
        reverse=True,
    )


def get_dn_status_delivery_counts(
    db: Session,
    *,
    lsp: Optional[str] = None,
    plan_mos_date: Optional[str] = None,
) -> List[tuple[str, int]]:
    """Return DN counts grouped by status_delivery with optional filtering."""

    status_expr = func.coalesce(func.nullif(func.trim(DN.status_delivery), ""), "NO STATUS")

    query = db.query(status_expr.label("status_delivery"), func.count(DN.id).label("count")).filter(_ACTIVE_DN_EXPR)

    trimmed_lsp = lsp.strip() if lsp else None
    if trimmed_lsp:
        query = query.filter(func.trim(DN.lsp) == trimmed_lsp)

    trimmed_plan_mos_date = plan_mos_date.strip() if plan_mos_date else None
    if trimmed_plan_mos_date:
        query = query.filter(func.trim(DN.plan_mos_date) == trimmed_plan_mos_date)

    rows = query.group_by(status_expr).order_by(status_expr.asc()).all()

    return [(row.status_delivery, int(row.count)) for row in rows]


def get_dn_status_delivery_lsp_counts(
    db: Session,
    *,
    lsp: Optional[str] = None,
    plan_mos_date: Optional[str] = None,
) -> List[tuple[str, int, int]]:
    """Return DN counts grouped by LSP for PIC confirmed records.

    total_count counts DN rows where status_site equals ``PIC confirmed`` (case-insensitive).
    status_not_empty_count applies the same filter and requires status_delivery to be non-empty.
    """

    lsp_expr = func.coalesce(func.nullif(func.trim(DN.lsp), ""), "NO LSP")
    trimmed_plan_mos_date = plan_mos_date.strip() if plan_mos_date else None
    trimmed_lsp = lsp.strip() if lsp else None

    # Treat NULL/empty status_site as empty string, then compare lowercase
    status_site_normalized = func.lower(func.coalesce(func.trim(DN.status_site), ""))
    status_site_matches = status_site_normalized != "pic not confirmed"
    status_delivery_trimmed = func.trim(DN.status_delivery)
    # Consider status_delivery present only if it's non-empty and not the sentinel "No Status" (case-insensitive)
    status_delivery_present = and_(
        DN.status_delivery.isnot(None),
        func.length(status_delivery_trimmed) > 0,
        func.lower(status_delivery_trimmed) != "no status",
    )

    total_case = case((status_site_matches, 1), else_=0)
    status_not_empty_case = case((and_(status_site_matches, status_delivery_present), 1), else_=0)

    query = db.query(
        lsp_expr.label("lsp"),
        func.sum(total_case).label("total_count"),
        func.sum(status_not_empty_case).label("status_not_empty_count"),
    ).filter(_ACTIVE_DN_EXPR)

    if trimmed_plan_mos_date:
        query = query.filter(func.trim(DN.plan_mos_date) == trimmed_plan_mos_date)

    if trimmed_lsp:
        query = query.filter(func.trim(DN.lsp) == trimmed_lsp)

    rows = query.group_by(lsp_expr).order_by(lsp_expr.asc()).all()

    return [
        (
            row.lsp,
            int(row.total_count),
            int(row.status_not_empty_count),
        )
        for row in rows
    ]


def get_dn_latest_update_snapshots(
    db: Session,
    *,
    lsp: Optional[str] = None,
    include_deleted: bool = False,
) -> list[tuple[str | None, str | None, datetime | None]]:
    """Return latest DN record timestamps grouped by DN row.

    When ``include_deleted`` is ``True`` the result set will include DN rows that
    are soft-deleted (``is_deleted != 'N'``).
    """

    latest_record_subq = (
        db.query(
            DNRecord.dn_number.label("dn_number"),
            func.max(DNRecord.created_at).label("latest_record_created_at"),
        )
        .group_by(DNRecord.dn_number)
        .subquery()
    )

    query = db.query(
        DN.lsp,
        DN.plan_mos_date,
        latest_record_subq.c.latest_record_created_at,
    ).join(latest_record_subq, DN.dn_number == latest_record_subq.c.dn_number)

    if not include_deleted:
        query = query.filter(_ACTIVE_DN_EXPR)

    trimmed_lsp = lsp.strip() if isinstance(lsp, str) else None
    if trimmed_lsp:
        query = query.filter(func.trim(DN.lsp) == trimmed_lsp)

    rows = query.all()

    # If lsp is empty, only include rows whose lsp matches HTM.{alnum}-IDN
    trimmed_lsp = lsp.strip() if isinstance(lsp, str) else None
    if not trimmed_lsp:
        import re

        pattern = re.compile(r"^HTM\.[A-Za-z0-9]+-IDN$", re.IGNORECASE)
        filtered = [
            (
                row.lsp,
                row.plan_mos_date,
                row.latest_record_created_at,
            )
            for row in rows
            if isinstance(row.lsp, str) and pattern.match(row.lsp.strip())
        ]
        return filtered

    return [
        (
            row.lsp,
            row.plan_mos_date,
            row.latest_record_created_at,
        )
        for row in rows
    ]


def upsert_status_delivery_lsp_stats(
    db: Session,
    records: Sequence[dict[str, Any]],
) -> list[StatusDeliveryLspStat]:
    """Create or update hourly LSP summary statistic rows."""

    if not records:
        return []

    persisted: list[StatusDeliveryLspStat] = []
    for payload in records:
        lsp = payload.get("lsp")
        recorded_at = payload.get("recorded_at")
        if not lsp or recorded_at is None:
            continue

        existing = (
            db.query(StatusDeliveryLspStat)
            .filter(
                StatusDeliveryLspStat.lsp == lsp,
                StatusDeliveryLspStat.recorded_at == recorded_at,
            )
            .one_or_none()
        )

        if existing is None:
            existing = StatusDeliveryLspStat(**payload)
        else:
            existing.total_dn = payload.get("total_dn", existing.total_dn)
            existing.status_not_empty = payload.get("status_not_empty", existing.status_not_empty)
            existing.plan_mos_date = payload.get("plan_mos_date", existing.plan_mos_date)

        db.add(existing)
        persisted.append(existing)

    db.commit()

    for record in persisted:
        db.refresh(record)

    return persisted


def list_status_delivery_lsp_stats(
    db: Session,
    *,
    lsp: Optional[str] = None,
    limit: int = 5000,
) -> list[StatusDeliveryLspStat]:
    """Return stored LSP summary statistics ordered by newest first."""

    query = db.query(StatusDeliveryLspStat)

    trimmed_lsp = lsp.strip() if isinstance(lsp, str) else None
    if trimmed_lsp:
        query = query.filter(StatusDeliveryLspStat.lsp == trimmed_lsp)

    limit = max(1, min(int(limit or 1), 10000))

    return (
        query.order_by(
            StatusDeliveryLspStat.recorded_at.desc(),
            StatusDeliveryLspStat.lsp.asc(),
        )
        .limit(limit)
        .all()
    )


def get_driver_stats(
    db: Session,
    *,
    phone_number: Optional[str] = None,
) -> List[Tuple[str, int, int]]:
    """
    统计各个 phone_number 下的唯一 DN 数量和记录数量。

    注意：一个 DN 下面，status 重复的记录只计算一次。

    Returns:
        List of tuples: (phone_number, unique_dn_count, record_count)
    """
    # 使用子查询去重：对每个 (dn_number, phone_number, status_delivery) 组合只计算一次
    subquery = (
        db.query(
            DNRecord.phone_number,
            DNRecord.dn_number,
            DNRecord.status_delivery,
        )
        .filter(DNRecord.phone_number.isnot(None))
        .filter(DNRecord.phone_number != "")
        .distinct()
        .subquery()
    )

    # 统计每个 phone_number 的唯一 DN 数量和记录数量
    query = db.query(
        subquery.c.phone_number,
        func.count(func.distinct(subquery.c.dn_number)).label("unique_dn_count"),
        func.count().label("record_count"),
    ).group_by(subquery.c.phone_number)

    # 如果指定了 phone_number，则过滤
    if phone_number:
        trimmed = phone_number.strip()
        if trimmed:
            query = query.filter(subquery.c.phone_number == trimmed)

    # 按唯一 DN 数量降序排序
    query = query.order_by(func.count(func.distinct(subquery.c.dn_number)).desc())

    return query.all()
