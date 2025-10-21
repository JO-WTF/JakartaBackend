"""Helpers for querying and exporting early-bird DN records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from typing import Dict, Iterable, Optional, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.constants import EARLY_BIRD_AREA_THRESHOLDS
from app.models import DN, DNRecord
from app.utils.time import TZ_GMT7, parse_plan_mos_date


@dataclass(slots=True)
class EarlyBirdResult:
    """Summary data for a DN that satisfies the early-bird criteria."""

    dn: DN
    plan_date: date
    arrival_time: datetime
    cutoff_time: datetime
    arrival_status: str
    record: DNRecord


def _normalize_text_label(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    collapsed = " ".join(value.strip().split())
    return collapsed.lower() if collapsed else None


def _normalize_area_label(value: Optional[str]) -> Optional[str]:
    return _normalize_text_label(value)


def _build_filter_set(values: Optional[Sequence[str]], normalizer) -> Optional[set[str]]:
    if not values:
        return None
    normalized = [normalizer(value) for value in values if isinstance(value, str)]
    filtered = {item for item in normalized if item}
    return filtered or None


def _get_area_threshold(area: Optional[str]) -> Optional[int]:
    normalized = _normalize_area_label(area)
    if not normalized:
        return None
    return EARLY_BIRD_AREA_THRESHOLDS.get(normalized)


def _to_jakarta(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_GMT7)


def collect_early_bird_results(
    db: Session,
    *,
    start_date: date,
    end_date: date,
    region_filters: Optional[Sequence[str]] = None,
    area_filters: Optional[Sequence[str]] = None,
    lsp_filters: Optional[Sequence[str]] = None,
) -> list[EarlyBirdResult]:
    """Return early-bird DN records for the requested date range and filters."""

    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    region_filter_set = _build_filter_set(region_filters, _normalize_text_label)
    area_filter_set = _build_filter_set(area_filters, _normalize_area_label)
    lsp_filter_set = _build_filter_set(lsp_filters, _normalize_text_label)

    base_query = (
        db.query(DN)
        .filter(DN.plan_mos_date.isnot(None))
        .filter(func.length(func.trim(DN.plan_mos_date)) > 0)
    )

    candidates: Dict[str, dict[str, object]] = {}

    for dn in base_query:
        plan_date = parse_plan_mos_date(getattr(dn, "plan_mos_date", None))
        if plan_date is None or plan_date < start_date or plan_date > end_date:
            continue

        region_value = _normalize_text_label(getattr(dn, "region", None))
        if region_filter_set is not None and region_value not in region_filter_set:
            continue

        area_value_raw = getattr(dn, "area", None)
        area_value = _normalize_area_label(area_value_raw)
        if area_filter_set is not None and area_value not in area_filter_set:
            continue

        lsp_value = _normalize_text_label(getattr(dn, "lsp", None))
        if lsp_filter_set is not None and lsp_value not in lsp_filter_set:
            continue

        threshold_hour = _get_area_threshold(area_value_raw)
        if threshold_hour is None:
            continue

        candidates[dn.dn_number] = {
            "dn": dn,
            "plan_date": plan_date,
            "threshold_hour": threshold_hour,
        }

    if not candidates:
        return []

    dn_numbers = list(candidates.keys())
    normalized_status = func.upper(func.trim(DNRecord.status_delivery))
    arrival_records: Iterable[DNRecord] = (
        db.query(DNRecord)
        .filter(DNRecord.dn_number.in_(dn_numbers))
        .filter(DNRecord.status_delivery.isnot(None))
        .filter(normalized_status.in_(("ARRIVED AT SITE", "POD")))
        .order_by(DNRecord.dn_number.asc(), DNRecord.created_at.asc(), DNRecord.id.asc())
        .all()
    )

    latest_arrivals: Dict[str, dict[str, object]] = {}

    for record in arrival_records:
        candidate = candidates.get(record.dn_number)
        if candidate is None:
            continue

        arrival_time = _to_jakarta(record.created_at)
        if arrival_time is None or arrival_time.date() != candidate["plan_date"]:
            continue

        raw_status = (record.status_delivery or "").strip().upper()
        if raw_status not in {"ARRIVED AT SITE", "POD"}:
            continue

        updater = (record.updated_by or "").strip().lower()
        if updater != "driver":
            continue

        priority = 0 if raw_status == "ARRIVED AT SITE" else 1
        existing = latest_arrivals.get(record.dn_number)
        if (
            existing is None
            or priority < existing["priority"]
            or (priority == existing["priority"] and arrival_time > existing["arrival_time"])
        ):
            latest_arrivals[record.dn_number] = {
                "arrival_time": arrival_time,
                "priority": priority,
                "status": raw_status,
                "record": record,
            }

    if not latest_arrivals:
        return []

    results: list[EarlyBirdResult] = []

    for dn_number, candidate in candidates.items():
        arrival_meta = latest_arrivals.get(dn_number)
        if arrival_meta is None:
            continue

        arrival_time = arrival_meta["arrival_time"]
        record: DNRecord = arrival_meta["record"]  # type: ignore[assignment]
        status_label = arrival_meta["status"]
        cutoff_time = datetime.combine(
            candidate["plan_date"],
            time(candidate["threshold_hour"], 0, tzinfo=TZ_GMT7),
        )
        if arrival_time >= cutoff_time:
            continue

        results.append(
            EarlyBirdResult(
                dn=candidate["dn"],
                plan_date=candidate["plan_date"],
                arrival_time=arrival_time,
                cutoff_time=cutoff_time,
                arrival_status=status_label,
                record=record,
            )
        )

    results.sort(key=lambda item: (item.plan_date, item.arrival_time, item.dn.dn_number))
    return results


__all__ = [
    "EarlyBirdResult",
    "collect_early_bird_results",
    "_normalize_text_label",
    "_normalize_area_label",
]
