"""DN statistics endpoints."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone, date
from typing import Any, Optional, Sequence

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.crud import (
    get_dn_status_delivery_counts,
    get_dn_status_delivery_lsp_counts,
    get_dn_unique_field_values,
    get_dn_latest_update_snapshots,
    list_status_delivery_lsp_stats,
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
)
from app.constants import STANDARD_STATUS_DELIVERY_VALUES
from app.utils.time import TZ_GMT7

router = APIRouter(prefix="/api/dn")

NO_STATUS_LABEL = "No Status"
_BASE_STATUS_ORDER = list(STANDARD_STATUS_DELIVERY_VALUES) + [NO_STATUS_LABEL]
_STATUS_LOOKUP = {status.lower(): status for status in STANDARD_STATUS_DELIVERY_VALUES}
_STATUS_LOOKUP[NO_STATUS_LABEL.lower()] = NO_STATUS_LABEL


def _canonicalize_status_delivery(value: Optional[str]) -> str:
    if value is None:
        return NO_STATUS_LABEL
    collapsed = " ".join(value.split())
    if not collapsed:
        return NO_STATUS_LABEL
    canonical = _STATUS_LOOKUP.get(collapsed.lower())
    if canonical:
        return canonical
    return collapsed


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
    rows: Sequence[tuple[str | None, str | None, datetime | None]]
) -> list[StatusDeliveryLspUpdateRecord]:
    records_by_lsp: dict[str, dict[date, Counter]] = {}

    for raw_lsp, plan_mos_date, latest_created_at in rows:
        jakarta_dt = _to_jakarta(latest_created_at)
        if jakarta_dt is None:
            continue

        hour_bucket = jakarta_dt.replace(minute=0, second=0, microsecond=0)
        lsp_label = _normalize_lsp_label(raw_lsp, plan_mos_date)
        day = hour_bucket.date()

        day_map = records_by_lsp.setdefault(lsp_label, {})
        counter = day_map.setdefault(day, Counter())
        counter[hour_bucket] += 1

    raw_records: list[tuple[datetime, str, int]] = []

    for lsp_label, day_map in records_by_lsp.items():
        for day in sorted(day_map):
            hour_counter = day_map[day]
            running_total = 0
            for hour in sorted(hour_counter):
                running_total += hour_counter[hour]
                raw_records.append((hour, lsp_label, running_total))

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
    for status, count in raw_counts:
        canonical_status = _canonicalize_status_delivery(status)
        status_counts[canonical_status] = status_counts.get(canonical_status, 0) + count

    base_statuses = [status for status in _BASE_STATUS_ORDER if status]
    extra_statuses = [status for status in status_counts if status not in base_statuses]
    extra_statuses.sort()
    final_statuses = base_statuses + extra_statuses

    values = [status_counts.get(status, 0) for status in final_statuses]
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
        StatusDeliveryCount(status_delivery=status, count=count)
        for status, count in stats
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

    update_rows = get_dn_latest_update_snapshots(db, lsp=normalized_lsp)
    update_summary = _build_update_summary(update_rows)

    return StatusDeliveryLspSummaryHistoryResponse(
        data=StatusDeliveryLspSummaryHistoryData(
            by_plan_mos_date=plan_mos_records,
            by_update_date=update_summary,
        )
    )
