from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, validator

from app.utils.string import normalize_dn


class PMCreate(BaseModel):
    pm_name: str = Field(..., min_length=1)
    lng: Optional[str] = None
    lat: Optional[str] = None
    address: Optional[str] = None

    @validator("pm_name")
    def strip_pm_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("pm_name required")
        return v

    @validator("address")
    def strip_address(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        return v or None


class PMDelete(BaseModel):
    pm_name: str = Field(..., min_length=1)

    @validator("pm_name")
    def strip_pm_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("pm_name required")
        return v


class DNAction(BaseModel):
    pm_name: str = Field(..., min_length=1)
    dn_number: str = Field(...)

    @validator("pm_name")
    def strip_pm(cls, v: str) -> str:
        return v.strip()

    @validator("dn_number")
    def normalize_dn_field(cls, v: str) -> str:
        dn = normalize_dn(v)
        if not dn:
            raise ValueError("invalid dn_number")
        return dn


class DNQuery(BaseModel):
    dn_number: str

    @validator("dn_number")
    def normalize_dn_field(cls, v: str) -> str:
        dn = normalize_dn(v)
        if not dn:
            raise ValueError("invalid dn_number")
        return dn


class PMInventoryQuery(BaseModel):
    pm_name: str = Field(..., min_length=1)

    @validator("pm_name")
    def strip_pm_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("pm_name required")
        return v
