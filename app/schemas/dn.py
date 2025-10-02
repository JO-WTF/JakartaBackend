"""Pydantic models for DN endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import List

from pydantic import BaseModel, Field

__all__ = [
    "DNColumnExtensionRequest",
    "ArchiveMarkRequest",
    "StatusDeliveryCount",
    "StatusDeliveryLspSummary",
    "StatusDeliveryStatsResponse",
    "StatusDeliveryLspSummaryRecord",
    "StatusDeliveryLspUpdateRecord",
    "StatusDeliveryLspSummaryHistoryData",
    "StatusDeliveryLspSummaryHistoryResponse",
]


class DNColumnExtensionRequest(BaseModel):
    columns: List[str] = Field(..., description="DN table columns to ensure exist", min_length=1)


class ArchiveMarkRequest(BaseModel):
    threshold_days: int = Field(
        7,
        alias="thresholdDays",
        ge=0,
        description="Number of days before today that Plan MOS rows must precede to be archived.",
    )

    class Config:
        populate_by_name = True


class StatusDeliveryCount(BaseModel):
    status_delivery: str = Field(..., description="Status delivery label")
    count: int = Field(..., ge=0, description="Number of DN rows for the status")


class StatusDeliveryLspSummary(BaseModel):
    lsp: str = Field(..., description="Logistics service provider name")
    total_dn: int = Field(..., ge=0, description="Total DN rows for the LSP")
    status_not_empty: int = Field(
        ..., ge=0, description="DN rows for the LSP where status field is not empty"
    )


class StatusDeliveryStatsResponse(BaseModel):
    ok: bool = Field(True, description="Whether the request succeeded")
    data: List[StatusDeliveryCount] = Field(
        ..., description="Status delivery counts for the requested Plan MOS date"
    )
    total: int = Field(..., ge=0, description="Total DN rows matching the filters")
    lsp_summary: List[StatusDeliveryLspSummary] = Field(
        ..., description="Aggregated LSP summary metrics"
    )


class StatusDeliveryLspSummaryRecord(BaseModel):
    id: int = Field(..., description="Identifier of the recorded summary row")
    lsp: str = Field(..., description="Logistics service provider name")
    total_dn: int = Field(..., ge=0, description="Total DN rows counted in the snapshot")
    status_not_empty: int = Field(
        ..., ge=0, description="Rows within the snapshot whose status column is not empty"
    )
    plan_mos_date: str = Field(..., description="Plan MOS date used when generating the snapshot")
    recorded_at: datetime = Field(
        ..., description="Timestamp when the snapshot was captured"
    )


class StatusDeliveryLspUpdateRecord(BaseModel):
    id: int = Field(..., description="Sequential identifier for the aggregated update row")
    lsp: str = Field(..., description="Logistics service provider label")
    updated_dn: int = Field(..., ge=0, description="Cumulative DN count up to the captured hour")
    update_date: str = Field(..., description="Local Jakarta date for the update bucket")
    recorded_at: str = Field(..., description="Localized hour bucket in Jakarta time (YYYY-MM-DD HH:mm:ss)")


class StatusDeliveryLspSummaryHistoryData(BaseModel):
    by_plan_mos_date: List[StatusDeliveryLspSummaryRecord] = Field(
        ..., description="Snapshot records grouped by Plan MOS date"
    )
    by_update_date: List[StatusDeliveryLspUpdateRecord] = Field(
        ..., description="Cumulative DN counts grouped by update hour"
    )


class StatusDeliveryLspSummaryHistoryResponse(BaseModel):
    ok: bool = Field(True, description="Whether the request succeeded")
    data: StatusDeliveryLspSummaryHistoryData = Field(
        ..., description="Historical status-delivery data broken down by Plan MOS date and update hour"
    )
