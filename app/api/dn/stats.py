"""DN statistics endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.google import SPREADSHEET_URL, create_gspread_client
from app.core.sheet import parse_date, process_all_sheets
from app.crud import (
    get_dn_status_delivery_counts,
    get_dn_status_delivery_lsp_counts,
    get_dn_unique_field_values,
)
from app.db import get_db
from app.schemas.dn import (
    StatusDeliveryCount,
    StatusDeliveryLspSummary,
    StatusDeliveryStatsResponse,
)

router = APIRouter(prefix="/api/dn")


@router.get("/stats/{date}")
async def get_dn_stats(date: str):
    gc = create_gspread_client()
    sh = gc.open_by_url(SPREADSHEET_URL)

    combined_df = process_all_sheets(sh)

    def _to_strf(value: Any) -> Any:
        parsed = parse_date(value)
        return parsed.strftime("%d-%b-%y") if isinstance(parsed, datetime) else value

    combined_df["plan_mos_date"] = combined_df["plan_mos_date"].apply(_to_strf)
    day_df = combined_df[combined_df["plan_mos_date"] == date]
    day_df["status_delivery"] = day_df["status_delivery"].apply(lambda x: x.upper() if x else "NO STATUS")

    pivot_df = (
        day_df.groupby(["plan_mos_date", "region", "status_delivery"])['dn_number'].nunique().unstack(fill_value=0)
    )

    all_statuses = [
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
    ]

    extra = list(set(pivot_df.columns.tolist()) - set(all_statuses))
    final_statuses = all_statuses + extra

    pivot_df = pivot_df.reindex(columns=final_statuses, fill_value=0)
    pivot_df["Total"] = pivot_df.sum(axis=1)

    table_df = pivot_df.reset_index()
    table_df.columns = ["date", "group"] + table_df.columns.to_list()[2:]

    raw_rows = [
        {"group": row["group"], "date": row["date"], "values": list(row)[2:]} for _, row in table_df.iterrows()
    ]

    return {"ok": True, "data": raw_rows}


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
