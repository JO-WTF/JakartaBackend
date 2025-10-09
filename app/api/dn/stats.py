"""DN statistics endpoints."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, date, timedelta, time
from typing import Any, Optional, Sequence

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.crud import (
    get_dn_status_delivery_counts,
    get_dn_status_delivery_lsp_counts,
    get_dn_unique_field_values,
    get_dn_latest_update_snapshots,
    list_status_delivery_lsp_stats,
    get_driver_stats,
)
from app.db import get_db
from app.schemas.dn import (
    StatusDeliveryCount,
    StatusDeliveryLspSummary,
    StatusDeliveryStatsResponse,
    StatusDeliveryLspSummaryRecord,
    StatusDeliveryLspSummaryHistoryData,
    StatusDeliveryLspSummaryHistoryResponse,
    StatusDeliveryLspUpdateRecord,
    DriverStatsRecord,
    DriverStatsResponse,
)
from app.constants import STANDARD_STATUS_DELIVERY_VALUES
from app.utils.time import TZ_GMT7

router = APIRouter(prefix="/api/dn")

NO_STATUS_LABEL = "No Status"
_BASE_STATUS_ORDER = list(STANDARD_STATUS_DELIVERY_VALUES) + [NO_STATUS_LABEL]
_STATUS_DELIVERY_LOOKUP = {status_delivery.lower(): status_delivery for status_delivery in STANDARD_STATUS_DELIVERY_VALUES}
_STATUS_DELIVERY_LOOKUP[NO_STATUS_LABEL.lower()] = NO_STATUS_LABEL


def _canonicalize_status_delivery(value: Optional[str]) -> str:
    if value is None:
        return NO_STATUS_LABEL
    collapsed = " ".join(value.split())
    if not collapsed:
        return NO_STATUS_LABEL
    canonical = _STATUS_DELIVERY_LOOKUP.get(collapsed.lower())
    if canonical:
        return canonical
    return collapsed


def _current_jakarta_hour() -> datetime:
    now = datetime.now(TZ_GMT7)
    return now.replace(minute=0, second=0, microsecond=0)


def _normalize_lsp_label(raw_lsp: Optional[str], plan_mos_date: Optional[str]) -> str:
    trimmed = (raw_lsp or "").strip()
    if not trimmed:
        base = "NO_LSP"
    else:
        upper = trimmed.upper()
        if upper in {"#N/A", "NO LSP", "NO_LSP"}:
            base = "NO_LSP"
        elif upper == "SUBCON":
            base = "Subcon"
        else:
            base = trimmed

    if base == "NO_LSP" and not (plan_mos_date or "").strip():
        return "NO_LSP_NO_PLAN_MOS_DATE"

    return base


def _to_jakarta(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_GMT7)


def _build_update_summary(
    rows: Sequence[tuple[str | None, str | None, datetime | None]],
    *,
    current_hour: datetime | None = None,
) -> list[StatusDeliveryLspUpdateRecord]:
    per_lsp_day_counts: dict[str, dict[date, list[int]]] = defaultdict(dict)

    for raw_lsp, plan_mos_date, latest_created_at in rows:
        jakarta_dt = _to_jakarta(latest_created_at)
        if jakarta_dt is None:
            continue

        lsp_label = _normalize_lsp_label(raw_lsp, plan_mos_date)
        day_bucket = jakarta_dt.date()
        hour_index = jakarta_dt.hour

        day_counts = per_lsp_day_counts[lsp_label].setdefault(day_bucket, [0] * 24)
        day_counts[hour_index] += 1

    reference_hour: datetime | None
    if current_hour is None:
        reference_hour = _current_jakarta_hour()
    else:
        localized = _to_jakarta(current_hour)
        reference_hour = (
            localized.replace(minute=0, second=0, microsecond=0)
            if localized is not None
            else None
        )

    reference_day = reference_hour.date() if reference_hour else None

    raw_records: list[tuple[datetime, str, int]] = []

    for lsp_label, day_map in per_lsp_day_counts.items():
        if not day_map:
            continue

        sorted_days = sorted(day_map)
        first_day = sorted_days[0]
        last_day = sorted_days[-1]
        if reference_day:
            last_day = max(last_day, reference_day)

        current_day = first_day
        while current_day <= last_day:
            hours = day_map.get(current_day, [0] * 24)
            max_hour = 23
            if reference_day and current_day == reference_day:
                max_hour = reference_hour.hour

            running_total = 0
            for hour_idx in range(max_hour + 1):
                running_total += hours[hour_idx]
                hour_dt = datetime.combine(
                    current_day,
                    time(hour_idx, 0, 0, tzinfo=TZ_GMT7),
                )
                raw_records.append((hour_dt, lsp_label, running_total))

            current_day += timedelta(days=1)

    raw_records.sort(key=lambda item: (item[0], item[1]))

    update_records: list[StatusDeliveryLspUpdateRecord] = []
    for idx, (hour, lsp_label, running_total) in enumerate(raw_records, start=1):
        update_records.append(
            StatusDeliveryLspUpdateRecord(
                id=idx,
                lsp=lsp_label,
                updated_dn=running_total,
                update_date=hour.strftime("%d %b %y"),
                recorded_at=hour.strftime("%Y-%m-%d %H:%M:%S"),
            )
        )

    return update_records


@router.get("/stats/{date}")
def get_dn_stats(date: str, db: Session = Depends(get_db)):
    normalized_date = date.strip()
    if not normalized_date:
        return {"ok": True, "data": []}

    raw_counts = get_dn_status_delivery_counts(
        db, plan_mos_date=normalized_date
    )

    status_counts: dict[str, int] = {}
    for status_delivery, count in raw_counts:
        canonical_status = _canonicalize_status_delivery(status_delivery)
        status_counts[canonical_status] = status_counts.get(canonical_status, 0) + count

    base_statuses = [status_delivery for status_delivery in _BASE_STATUS_ORDER if status_delivery]
    extra_statuses = [status_delivery for status_delivery in status_counts if status_delivery not in base_statuses]
    extra_statuses.sort()
    final_statuses = base_statuses + extra_statuses

    values = [status_counts.get(status_delivery, 0) for status_delivery in final_statuses]
    total_count = sum(status_counts.values())
    values.append(total_count)

    row = {
        "group": "Total",
        "date": normalized_date,
        "values": values,
    }

    return {"ok": True, "data": [row]}


@router.get("/filters")
def get_dn_filter_options(db: Session = Depends(get_db)):
    values, total = get_dn_unique_field_values(db)
    data: dict[str, Any] = {**values, "total": total}
    if "status_delivery" in data:
        data.setdefault("status_deliver", data["status_delivery"])  # 兼容字段
    return {"ok": True, "data": data}


@router.get("/status-delivery/stats", response_model=StatusDeliveryStatsResponse)
def get_dn_status_delivery_stats(
    lsp: Optional[str] = Query(default=None),
    plan_mos_date: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    normalized_lsp = lsp.strip() if lsp else None
    normalized_plan_mos_date = (plan_mos_date.strip() if plan_mos_date else None) or datetime.now().strftime("%d %b %y")

    stats = get_dn_status_delivery_counts(
        db, lsp=normalized_lsp, plan_mos_date=normalized_plan_mos_date
    )
    total = sum(count for _, count in stats)

    data = [
        StatusDeliveryCount(status_delivery=status_delivery, count=count)
        for status_delivery, count in stats
    ]

    lsp_stats = get_dn_status_delivery_lsp_counts(
        db, lsp=normalized_lsp, plan_mos_date=normalized_plan_mos_date
    )
    lsp_summary = [
        StatusDeliveryLspSummary(
            lsp=lsp_value,
            total_dn=total_count,
            status_not_empty=status_count,
        )
        for lsp_value, total_count, status_count in lsp_stats
    ]

    return StatusDeliveryStatsResponse(
        data=data,
        total=total,
        lsp_summary=lsp_summary,
    )


@router.get(
    "/status-delivery/lsp-summary-records",
    response_model=StatusDeliveryLspSummaryHistoryResponse,
)
def get_status_delivery_lsp_summary_records(
    lsp: Optional[str] = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=10000),
    db: Session = Depends(get_db),
):
    normalized_lsp = lsp.strip() if lsp else None

    records = list_status_delivery_lsp_stats(
        db, lsp=normalized_lsp, limit=limit
    )

    plan_mos_records = [
        StatusDeliveryLspSummaryRecord(
            id=record.id,
            lsp=record.lsp,
            total_dn=record.total_dn,
            status_not_empty=record.status_not_empty,
            plan_mos_date=record.plan_mos_date,
            recorded_at=record.recorded_at,
        )
        for record in records
    ]

    update_rows = get_dn_latest_update_snapshots(
        db,
        lsp=normalized_lsp,
        include_deleted=True,
    )
    update_summary = _build_update_summary(update_rows)

    return StatusDeliveryLspSummaryHistoryResponse(
        data=StatusDeliveryLspSummaryHistoryData(
            by_plan_mos_date=plan_mos_records,
            by_update_date=update_summary,
        )
    )


@router.get(
    "/status_delivery/by-driver",
    response_model=DriverStatsResponse,
)
def get_driver_statistics(
    phone_number: Optional[str] = Query(default=None, description="Filter by specific phone number"),
    db: Session = Depends(get_db),
):
    """
    统计各个司机（按 phone_number）的 DN 处理情况。
    
    统计规则：
    - 仅统计 phone_number 非空的记录
    - 一个 DN 下面，相同 status_delivery 的记录只计算一次
    - 返回每个司机的唯一 DN 数量和去重后的记录数量
    """
    normalized_phone = phone_number.strip() if phone_number else None
    
    stats = get_driver_stats(db, phone_number=normalized_phone)
    
    data = [
        DriverStatsRecord(
            phone_number=phone,
            unique_dn_count=unique_dn,
            record_count=record_count,
        )
        for phone, unique_dn, record_count in stats
    ]
    
    return DriverStatsResponse(
        data=data,
        total_drivers=len(data),
    )
