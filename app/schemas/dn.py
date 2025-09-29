"""Pydantic models for DN endpoints."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

__all__ = [
    "DNColumnExtensionRequest",
    "ArchiveMarkRequest",
    "StatusDeliveryCount",
    "StatusDeliveryLspSummary",
    "StatusDeliveryStatsResponse",
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
