"""定义 FastAPI 所用的请求与响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    """所有 API 模型的基础类，启用 ORM 支持。"""

    model_config = ConfigDict(from_attributes=True)


class APIResponse(APIModel):
    """约定所有响应对象均包含 ``ok`` 字段。"""

    ok: bool = True


class OperationResult(APIResponse):
    id: Optional[int] = None
    photo: Optional[str] = None


class BatchOperationBase(APIModel):
    status: str
    success_count: int
    failure_count: int
    failure_details: Dict[str, str] = Field(default_factory=dict)
    errmessage: Optional[str] = None


class DNBatchOperationResult(BatchOperationBase):
    success_dn_numbers: List[str] = Field(default_factory=list)


class DNRecordSchema(APIModel):
    id: int
    dn_number: str
    du_id: Optional[str] = None
    status: Optional[str] = None
    remark: Optional[str] = None
    photo_url: Optional[str] = None
    lng: Optional[str] = None
    lat: Optional[str] = None
    created_at: Optional[datetime] = None


class DNEntitySchema(APIModel):
    id: int
    dn_number: str
    status: Optional[str] = None
    remark: Optional[str] = None
    photo_url: Optional[str] = None
    du_id: Optional[str] = None
    lng: Optional[str] = None
    lat: Optional[str] = None
    created_at: Optional[datetime] = None


class DNListItem(APIModel):
    model_config = ConfigDict(extra="allow")

    id: int
    dn_number: str
    created_at: Optional[datetime] = None
    status: Optional[str] = None
    remark: Optional[str] = None
    photo_url: Optional[str] = None
    lng: Optional[str] = None
    lat: Optional[str] = None
    latest_record_created_at: Optional[datetime] = None


class PaginatedResponse(APIResponse):
    total: int
    page: int
    page_size: int
    items: List[Any]


class DNStatusDeliveryStat(APIModel):
    status_delivery: str
    count: int


class DNFilterOptions(APIModel):
    data: Dict[str, Any]


class DNSyncLogEntry(APIModel):
    id: int
    status: str
    synced_count: int
    dn_numbers: List[str] = Field(default_factory=list)
    message: Optional[str] = None
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None
    created_at: Optional[datetime] = None


class DNSyncLogResponse(APIResponse):
    data: Optional[DNSyncLogEntry] = None


class DNColumnExtensionRequest(BaseModel):
    columns: Sequence[str]


class DNBatchCreateRequest(BaseModel):
    dn_numbers: Sequence[str]


class DNEditPayload(BaseModel):
    status: Optional[str] = None
    remark: Optional[str] = None
    du_id: Optional[str] = Field(default=None, alias="duId")

    model_config = ConfigDict(populate_by_name=True)


class DNBatchQuery(BaseModel):
    dn_number: Optional[List[str]] = None


class DNStatsRow(APIModel):
    group: str
    date: str
    values: List[int]


class DNStatsResponse(APIResponse):
    data: List[DNStatsRow]


class DNFilterResponse(APIResponse):
    data: Dict[str, Any]


class DNStatusDeliveryResponse(APIResponse):
    data: List[DNStatusDeliveryStat]
    total: int


class DNColumnExtendResponse(APIResponse):
    added_columns: List[str]
    columns: List[str]


class DNRecordListResponse(APIResponse):
    items: List[DNRecordSchema]
    total: Optional[int] = None


class DNListResponse(APIResponse):
    data: List[DNListItem]


class DNSyncResponse(APIResponse):
    synced_count: int = 0
    dn_numbers: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    errorInfo: Optional[str] = None


def serialize_dn_records(records: Sequence[Any]) -> List[DNRecordSchema]:
    """将 ORM/字典对象转换为 ``DNRecordSchema``。"""

    return [DNRecordSchema.model_validate(record) for record in records]


def serialize_dn_entities(records: Sequence[Any]) -> List[DNEntitySchema]:
    """序列化 DN 主表实体数据。"""

    return [DNEntitySchema.model_validate(record) for record in records]


def serialize_dn_list_items(rows: Sequence[Dict[str, Any]]) -> List[DNListItem]:
    """将包含扩展列的行数据转换为 ``DNListItem``。"""

    return [DNListItem.model_validate(row) for row in rows]


def build_paginated_response(
    *,
    items: Sequence[Any],
    total: int,
    page: int,
    page_size: int,
) -> PaginatedResponse:
    """根据分页元数据构建统一的响应结构。"""

    return PaginatedResponse(
        ok=True,
        total=total,
        page=page,
        page_size=page_size,
        items=list(items),
    )
