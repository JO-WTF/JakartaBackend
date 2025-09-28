"""Pydantic schemas used by DN endpoints."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class DNColumnExtensionRequest(BaseModel):
    columns: List[str] = Field(
        ...,
        description="DN table columns to ensure exist",
        min_length=1,
    )


class ArchiveMarkRequest(BaseModel):
    threshold_days: int = Field(
        7,
        alias="thresholdDays",
        ge=0,
        description=(
            "Number of days before today that Plan MOS rows must precede to be "
            "archived."
        ),
    )

    class Config:
        populate_by_name = True


__all__ = ["DNColumnExtensionRequest", "ArchiveMarkRequest"]
