"""Utilities for recording hourly status-delivery LSP summaries."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Sequence

from app.crud import (
    get_dn_status_delivery_lsp_counts,
    upsert_status_delivery_lsp_stats,
)
from app.db import SessionLocal
from app.models import StatusDeliveryLspStat
from app.utils.logging import logger

PLAN_MOS_DATE_FORMAT = "%d %b %y"

__all__ = [
    "PLAN_MOS_DATE_FORMAT",
    "capture_status_delivery_lsp_summary",
    "scheduled_status_delivery_lsp_summary_capture",
]


def capture_status_delivery_lsp_summary(
    plan_mos_date: str | None = None,
) -> Sequence[StatusDeliveryLspStat]:
    """Collect and persist the latest LSP summary statistics."""

    normalized_plan_mos_date = (plan_mos_date.strip() if plan_mos_date else None) or datetime.now().strftime(PLAN_MOS_DATE_FORMAT)

    recorded_at = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    db = SessionLocal()
    try:
        lsp_stats = get_dn_status_delivery_lsp_counts(
            db, plan_mos_date=normalized_plan_mos_date
        )

        if not lsp_stats:
            logger.info(
                "No LSP summary data available for plan MOS date %s", normalized_plan_mos_date
            )
            return []

        records = [
            {
                "lsp": lsp_value,
                "total_dn": total_count,
                "status_not_empty": status_count,
                "plan_mos_date": normalized_plan_mos_date,
                "recorded_at": recorded_at,
            }
            for lsp_value, total_count, status_count in lsp_stats
        ]

        persisted = upsert_status_delivery_lsp_stats(db, records)
        logger.info(
            "Stored %d LSP summary rows for plan MOS date %s at %s",
            len(persisted),
            normalized_plan_mos_date,
            recorded_at.isoformat(),
        )
        return persisted
    finally:
        db.close()


async def scheduled_status_delivery_lsp_summary_capture() -> None:
    """Background job entrypoint for hourly LSP summary snapshots."""

    try:
        await asyncio.to_thread(capture_status_delivery_lsp_summary)
    except Exception:
        logger.exception("Failed to record status-delivery LSP summary")
