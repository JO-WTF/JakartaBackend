"""提供 DN 相关接口的路由模块。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from sqlalchemy.orm import Session

from ..constants import VALID_STATUS_DESCRIPTION
from ..crud import UnitOfWork
from ..db import get_db
from ..dn_columns import extend_dn_table_columns, get_sheet_columns
from ..schemas import (
    DNColumnExtendResponse,
    DNColumnExtensionRequest,
    DNFilterResponse,
    DNListResponse,
    DNRecordListResponse,
    DNStatusDeliveryResponse,
    DNStatusDeliveryStat,
    DNBatchCreateRequest,
    DNBatchOperationResult,
    DNEditPayload,
    OperationResult,
    PaginatedResponse,
    build_paginated_response,
    serialize_dn_list_items,
    serialize_dn_records,
)
from ..services.dn_service import DNService
from ..utils.files import save_upload_file
from ..utils.normalization import (
    DU_RE,
    DN_RE,
    collect_query_values,
    normalize_batch_dn_numbers,
    normalize_dn,
    normalize_du,
    strip_optional_string,
)

router = APIRouter(prefix="/api/dn", tags=["DN"])


def _build_dn_rows(
    *,
    dn_items: List[Any],
    latest_records: Dict[str, Any],
    sheet_columns: List[str],
) -> List[Dict[str, Any]]:
    """将 DN ORM 结果与最新记录信息融合，便于序列化。"""

    rows: List[Dict[str, Any]] = []
    for item in dn_items:
        row: Dict[str, Any] = {
            "id": item.id,
            "dn_number": item.dn_number,
            "created_at": item.created_at,
            "status": item.status,
            "remark": item.remark,
            "photo_url": item.photo_url,
            "lng": item.lng,
            "lat": item.lat,
            "du_id": item.du_id,
        }
        for column in sheet_columns:
            if column == "dn_number":
                continue
            row[column] = getattr(item, column, None)
        latest = latest_records.get(item.dn_number)
        row["latest_record_created_at"] = getattr(latest, "created_at", None)
        rows.append(row)
    return rows


@router.post("/columns/extend", response_model=DNColumnExtendResponse)
def extend_dn_columns_api(
    request: DNColumnExtensionRequest = Body(...),
    db: Session = Depends(get_db),
) -> DNColumnExtendResponse:
    """根据 Google Sheet 传入的列名扩展 DN 表结构。"""

    try:
        added = extend_dn_table_columns(db, request.columns)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return DNColumnExtendResponse(ok=True, added_columns=added, columns=get_sheet_columns())


@router.post("/update", response_model=OperationResult)
def create_dn_update(
    dnNumber: str = Form(...),
    status: str = Form(...),
    remark: str | None = Form(None),
    duId: str | None = Form(None),
    photo: UploadFile | None = File(None),
    lng: str | float | None = Form(None),
    lat: str | float | None = Form(None),
    db: Session = Depends(get_db),
) -> OperationResult:
    """处理多部分表单，写入一条新的 DN 记录。"""

    photo_url = save_upload_file(photo)

    lng_str = str(lng) if lng else None
    lat_str = str(lat) if lat else None

    remark_value = strip_optional_string(remark)
    du_value = strip_optional_string(duId)

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        try:
            record = service.create_update(
                dn_number=dnNumber,
                status=status,
                remark=remark_value,
                du_id=du_value,
                photo_url=photo_url,
                lng=lng_str,
                lat=lat_str,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return OperationResult(ok=True, id=record.id, photo=photo_url)


@router.post("/batch_update", response_model=DNBatchOperationResult)
def batch_update_dn(
    payload: DNBatchCreateRequest = Body(...),
    db: Session = Depends(get_db),
):
    """批量创建 DN 编号，并返回逐项失败信息。"""

    if not payload.dn_numbers:
        return DNBatchOperationResult(
            status="fail",
            success_count=0,
            failure_count=0,
            success_dn_numbers=[],
            failure_details={},
            errmessage="DN number 列表为空",
        )

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        result = service.batch_create(payload.dn_numbers)

    return DNBatchOperationResult(
        status=result["status"],
        success_count=result["success_count"],
        failure_count=result["failure_count"],
        success_dn_numbers=result.get("success_dn_numbers", []),
        failure_details=result.get("failure_details", {}),
        errmessage=result.get("errmessage"),
    )


@router.get("/search", response_model=PaginatedResponse)
def search_dn_records(
    dn_number: Optional[str] = Query(None, description="精确 DN number"),
    du_id: Optional[str] = Query(None, description="关联 DU ID"),
    status: Optional[str] = Query(
        None, description=f"状态过滤，可选: {VALID_STATUS_DESCRIPTION}"
    ),
    remark: Optional[str] = Query(None, description="备注关键词(模糊)"),
    has_photo: Optional[bool] = Query(None, description="是否必须带附件 true/false"),
    date_from: Optional[datetime] = Query(None, description="起始时间(ISO 8601)"),
    date_to: Optional[datetime] = Query(None, description="结束时间(ISO 8601)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedResponse:
    """根据多种筛选条件分页查询 DN 历史记录。"""

    normalized_dn = None
    if dn_number:
        normalized_dn = normalize_dn(dn_number)
        if not DN_RE.fullmatch(normalized_dn):
            raise HTTPException(status_code=400, detail=f"Invalid DN number: {dn_number}")

    normalized_du = None
    if du_id:
        normalized_du = normalize_du(du_id)
        if not DU_RE.fullmatch(normalized_du):
            raise HTTPException(status_code=400, detail=f"Invalid DU ID: {du_id}")

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        total, items = service.search_records(
            dn_number=normalized_dn,
            du_id=normalized_du,
            status=status,
            remark_keyword=remark,
            has_photo=has_photo,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )

    serialized_items = serialize_dn_records(items)
    return build_paginated_response(
        items=serialized_items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.delete("/{dn_number}", response_model=OperationResult)
def remove_dn(
    dn_number: str,
    db: Session = Depends(get_db),
) -> OperationResult:
    """删除 DN 主记录及其所有历史。"""

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        try:
            deleted = service.delete_dn(dn_number)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if not deleted:
        raise HTTPException(status_code=404, detail="DN not found")
    return OperationResult(ok=True)


@router.get("/{dn_number}", response_model=DNRecordListResponse)
def get_dn_records(
    dn_number: str,
    db: Session = Depends(get_db),
) -> DNRecordListResponse:
    """返回指定 DN 的按时间排序的历史记录。"""

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        try:
            items = service.list_records(dn_number)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    serialized_items = serialize_dn_records(items)
    return DNRecordListResponse(ok=True, items=serialized_items)


@router.get("/filters", response_model=DNFilterResponse)
def get_dn_filter_options(db: Session = Depends(get_db)) -> DNFilterResponse:
    """获取供前端筛选使用的 DN 字段去重值。"""

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        values, total = service.get_unique_field_values()

    data: Dict[str, Any] = {**values, "total": total}
    if "status_delivery" in data:
        data.setdefault("status_deliver", data["status_delivery"])

    return DNFilterResponse(ok=True, data=data)


@router.get("/status-delivery/stats", response_model=DNStatusDeliveryResponse)
def get_dn_status_delivery_stats(
    lsp: Optional[str] = Query(default=None),
    plan_mos_date: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
) -> DNStatusDeliveryResponse:
    """统计 ``status_delivery`` 各状态数量，供看板展示。"""

    normalized_lsp = lsp.strip() if lsp else None
    normalized_plan = plan_mos_date.strip() if plan_mos_date else None
    if not normalized_plan:
        normalized_plan = datetime.now().strftime("%d %b %y")

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        stats = service.get_status_delivery_counts(
            lsp=normalized_lsp, plan_mos_date=normalized_plan
        )

    total = sum(count for _, count in stats)
    stat_models = [
        DNStatusDeliveryStat(status_delivery=status, count=count)
        for status, count in stats
    ]

    return DNStatusDeliveryResponse(ok=True, data=stat_models, total=total)


@router.get("/records", response_model=DNRecordListResponse)
def get_all_dn_records(db: Session = Depends(get_db)) -> DNRecordListResponse:
    """获取每个 DN 对应的最新历史记录。"""

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        items = service.list_all_records()
    serialized_items = serialize_dn_records(items)
    return DNRecordListResponse(ok=True, total=len(serialized_items), items=serialized_items)


@router.get("/list/batch", response_model=PaginatedResponse)
def batch_search_dn_list(
    dn_number: Optional[List[str]] = Query(None, description="重复 dn_number 或逗号分隔"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedResponse:
    """根据给定 DN 编号列表查询主表实体并分页。"""

    try:
        normalized_numbers = normalize_batch_dn_numbers(dn_number or [])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        total, items = service.list_dn_by_numbers(
            normalized_numbers, page=page, page_size=page_size
        )
        latest_records = (
            service.get_latest_records_map([item.dn_number for item in items]) if items else {}
        )

    sheet_columns = get_sheet_columns()
    rows = _build_dn_rows(
        dn_items=items, latest_records=latest_records, sheet_columns=sheet_columns
    )
    serialized_items = serialize_dn_list_items(rows)

    return build_paginated_response(
        items=serialized_items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.put("/update/{record_id}", response_model=OperationResult)
def edit_dn_record(
    record_id: int,
    status: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    duId: Optional[str] = Form(None),
    photo: UploadFile | None = File(None),
    json_body: DNEditPayload | None = Body(None),
    db: Session = Depends(get_db),
) -> OperationResult:
    """通过表单或 JSON 更新 DN 历史记录的可变字段。"""

    du_id_provided = duId is not None

    if json_body:
        payload_data = json_body.model_dump(exclude_unset=True)
        if "status" in payload_data:
            status = payload_data["status"]
        if "remark" in payload_data:
            remark = payload_data["remark"]
        if "du_id" in payload_data:
            duId = payload_data["du_id"]
            du_id_provided = True

    status_value = strip_optional_string(status)
    remark_value = strip_optional_string(remark)
    if remark_value is not None and len(remark_value) > 1000:
        raise HTTPException(status_code=400, detail="remark too long (max 1000 chars)")

    du_value = strip_optional_string(duId)

    photo_url = save_upload_file(photo)

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        try:
            record = service.update_record(
                record_id,
                status=status_value,
                remark=remark_value,
                photo_url=photo_url,
                du_id=du_value,
                du_id_provided=du_id_provided,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    return OperationResult(ok=True, id=record.id, photo=photo_url)


@router.delete("/update/{record_id}", response_model=OperationResult)
def delete_dn_record(
    record_id: int,
    db: Session = Depends(get_db),
) -> OperationResult:
    """删除一条 DN 历史记录。"""

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        deleted = service.delete_record(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Record not found")
    return OperationResult(ok=True)


@router.get("/list", response_model=DNListResponse)
def get_dn_list(db: Session = Depends(get_db)) -> DNListResponse:
    """返回包含指定表格列的全部 DN 实体。"""

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        items = service.list_dn_entities()
        if not items:
            return DNListResponse(ok=True, data=[])
        latest_records = service.get_latest_records_map([item.dn_number for item in items])

    sheet_columns = get_sheet_columns()
    rows = _build_dn_rows(
        dn_items=items, latest_records=latest_records, sheet_columns=sheet_columns
    )
    return DNListResponse(ok=True, data=serialize_dn_list_items(rows))


@router.get("/list/search", response_model=PaginatedResponse)
def search_dn_list_api(
    date: Optional[List[str]] = Query(None, description="Plan MOS date"),
    dn_number: str | None = Query(None, description="DN number"),
    du_id: str | None = Query(None, description="关联 DU ID"),
    status_delivery: Optional[List[str]] = Query(None, description="Status delivery"),
    status_values_param: Optional[List[str]] = Query(
        None,
        alias="status",
        description="Status",
    ),
    status_not_empty: bool | None = Query(
        None,
        description="仅返回状态不为空的 DN 记录",
    ),
    has_coordinate: bool | None = Query(
        None,
        description="根据是否存在经纬度筛选 DN 记录",
    ),
    lsp: Optional[List[str]] = Query(None, description="LSP"),
    region: Optional[List[str]] = Query(None, description="Region"),
    area: str | None = Query(None, description="Area"),
    status_wh: Optional[List[str]] = Query(None, description="Status WH"),
    subcon: Optional[List[str]] = Query(None, description="Subcon"),
    project: str | None = Query(None, description="Project request"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedResponse:
    """提供与表格类似的高级筛选能力查询 DN 列表。"""

    normalized_dn = normalize_dn(dn_number) if dn_number else None
    if normalized_dn and not DN_RE.fullmatch(normalized_dn):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    normalized_du = normalize_du(du_id) if du_id else None
    if normalized_du and not DU_RE.fullmatch(normalized_du):
        raise HTTPException(status_code=400, detail=f"Invalid DU ID: {normalized_du}")

    plan_mos_dates = collect_query_values(date)
    status_delivery_values = collect_query_values(status_delivery)
    status_values = collect_query_values(status_values_param)
    lsp_values = collect_query_values(lsp)
    region_values = collect_query_values(region)
    status_wh_values = collect_query_values(status_wh)
    subcon_values = collect_query_values(subcon)
    area_value = area.strip() if area else None
    project_value = project.strip() if project else None

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        total, items = service.search_dn_list(
            plan_mos_dates=plan_mos_dates,
            dn_number=normalized_dn,
            du_id=normalized_du,
            status_delivery_values=status_delivery_values,
            status_values=status_values,
            status_not_empty=status_not_empty,
            has_coordinate=has_coordinate,
            lsp_values=lsp_values,
            region_values=region_values,
            area=area_value,
            status_wh_values=status_wh_values,
            subcon_values=subcon_values,
            project_request=project_value,
            page=page,
            page_size=page_size,
        )

        latest_records = (
            service.get_latest_records_map([item.dn_number for item in items]) if items else {}
        )

    sheet_columns = get_sheet_columns()

    rows = _build_dn_rows(
        dn_items=items, latest_records=latest_records, sheet_columns=sheet_columns
    )
    serialized_items = serialize_dn_list_items(rows)

    return build_paginated_response(
        items=serialized_items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/batch", response_model=PaginatedResponse)
def batch_get_dn_records(
    dn_number: Optional[List[str]] = Query(None, description="重复 dn_number 或逗号分隔"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedResponse:
    """批量获取多个 DN 的历史记录并分页。"""

    try:
        normalized_numbers = normalize_batch_dn_numbers(dn_number or [])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    with UnitOfWork(db) as uow:
        service = DNService(uow)
        total, items = service.list_records_by_numbers(
            normalized_numbers, page=page, page_size=page_size
        )

    serialized_items = serialize_dn_records(items)
    return build_paginated_response(
        items=serialized_items,
        total=total,
        page=page,
        page_size=page_size,
    )
