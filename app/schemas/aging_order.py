from urllib.parse import unquote_plus

from pydantic import BaseModel, Field, validator


class AgingOrderPmUpdate(BaseModel):
    order_name: str = Field(..., min_length=1)
    pm_location: str = Field(..., min_length=1)

    @validator("order_name")
    def _strip_order_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("order_name is required")
        return value

    @validator("pm_location")
    def _normalize_pm_location(cls, value: str) -> str:
        value = unquote_plus(value)
        value = value.strip()
        if not value:
            raise ValueError("pm_location is required")
        return value


class AgingOrderQuery(BaseModel):
    order_name: str = Field(..., min_length=1)

    @validator("order_name")
    def _strip_order(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("order_name is required")
        return value


class AgingOrderPmLocationQuery(BaseModel):
    pm_location: str = Field(..., min_length=1)

    @validator("pm_location")
    def _strip_pm_location(cls, value: str) -> str:
        value = unquote_plus(value)
        value = value.strip()
        if not value:
            raise ValueError("pm_location is required")
        return value
