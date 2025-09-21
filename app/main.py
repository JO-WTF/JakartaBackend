# app/main.py
from fastapi import Body, FastAPI, UploadFile, File, Form, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional, List, Any
from datetime import datetime, timedelta
import asyncio
import re, os, unicodedata

from .settings import settings
from .db import Base, engine, get_db, SessionLocal
from .crud import (
    ensure_du,
    add_record,
    list_records,
    search_records,
    list_records_by_du_ids,
    update_record,
    delete_record,
    get_existing_du_ids,
    ensure_dn,
    add_dn_record,
    list_dn_records,
    search_dn_records,
    list_dn_records_by_dn_numbers,
    update_dn_record,
    delete_dn_record,
    get_existing_dn_numbers,
    get_latest_dn_records_map,
    search_dn_list,
    create_dn_sync_log,
    get_latest_dn_sync_log,
)
from .storage import save_file
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import logging, traceback
import gspread
import pandas as pd
from .models import DN

# ====== 启动与静态 ======
os.makedirs(settings.storage_disk_path, exist_ok=True)
app = FastAPI(title="DU Backend API", version="1.1.0")

logger = logging.getLogger("uvicorn.error")

SHEET_SYNC_INTERVAL_SECONDS = 300
_scheduler: AsyncIOScheduler | None = None
_SHEET_SYNC_JOB_ID = "dn_sheet_sync"

@app.exception_handler(Exception)
async def all_exception_handler(request, exc):
    logger.error("Unhandled error on %s %s\n%s",
                 request.method, request.url.path, traceback.format_exc())
    return JSONResponse(status_code=500, content={"ok": False, "error": "internal_error", "errorInfo":traceback.format_exc()})


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.storage_driver != "s3":
    os.makedirs(settings.storage_disk_path, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=settings.storage_disk_path, check_dir=False), name="uploads")

Base.metadata.create_all(bind=engine)

# ====== 校验与清洗 ======
DU_RE = re.compile(r"^.+$")
DN_RE = re.compile(r"^.+$")

VALID_STATUSES: tuple[str, ...] = (
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
    "NEW MOS",
    "开始运输",
    "运输中",
    "已到达",
    "过夜",
)

VALID_STATUS_DESCRIPTION = ", ".join(VALID_STATUSES)

def normalize_du(s: str) -> str:
    """NFC 规整、去零宽、全角转半角、去空白、统一大写"""
    if not s:
        return ""
    
    # NFC 规整
    s = unicodedata.normalize("NFC", s)
    
    # 去零宽、BOM字符
    s = s.replace("\u200b", "").replace("\ufeff", "")
    
    # 去空白并统一为大写
    s = s.strip().upper()
    
    # 转换全角数字为半角
    trans = str.maketrans("０１２３４５６７８９", "0123456789")
    s = s.translate(trans)
    
    # 转换全角字母为半角
    trans_fullwidth_letters = str.maketrans(
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ", 
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    )
    s = s.translate(trans_fullwidth_letters)

    return s


def normalize_dn(s: str) -> str:
    return normalize_du(s)

# ====== 基础健康检查 ======
@app.get("/")
def healthz():
    return {"ok": True, "message":"You can use admin panel now."}

# ====== 新建一条更新（原有） ======
@app.post("/api/du/update")
def update_du(
    duId: str = Form(...),
    status: str = Form(...),
    remark: str | None = Form(None),
    photo: UploadFile | None = File(None),
    lng: str | float | None = Form(None),
    lat: str | float | None = Form(None),
    db: Session = Depends(get_db),
):
    duId = normalize_du(duId)
    if not DU_RE.fullmatch(duId):
        raise HTTPException(status_code=400, detail="Invalid DU ID")
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    ensure_du(db, duId)

    photo_url = None
    if photo and photo.filename:
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")
    
    lng = str(lng) if lng else None
    lat = str(lat) if lat else None

    rec = add_record(db, du_id=duId, status=status, remark=remark, photo_url=photo_url, lng=lng, lat=lat)
    return {"ok": True, "id": rec.id, "photo": photo_url}


# ====== 批量新建多条更新（新） ======
@app.post("/api/du/batch_update")
def batch_update_du(
    du_ids: List[str] = Body(..., description="JSON array of DU IDs to create"),
    db: Session = Depends(get_db),
):
    """批量创建 DU 默认状态记录。

    **请求体格式**::

        {
            "du_ids": ["DU0001", "DU0002"]
        }

    **成功返回示例**（部分成功亦属于 OK）::

        {
            "status": "ok",
            "success_count": 2,
            "failure_count": 1,
            "success_duids": ["DU0001", "DU0002"],
            "failure_details": {
                "DU0003": "DU ID 已存在"
            }
        }

    **完全失败示例**::

        {
            "status": "fail",
            "success_count": 0,
            "failure_count": 2,
            "success_duids": [],
            "failure_details": {
                "<empty>": "无效的 DU ID",
                "DU0003": "DU ID 已存在"
            }
        }

    **当 du_ids 为空时**::

        {
            "status": "fail",
            "errmessage": "DU ID 列表为空",
            "success_count": 0,
            "failure_count": 0,
            "success_duids": [],
            "failure_details": {}
        }

    返回字段说明：
        * status 为 "ok" 表示至少有一条新增成功，否则为 "fail"；
        * success_count / failure_count 分别为成功、失败的 DU 数量；
        * success_duids 列举成功写入的 DU ID；
        * failure_details 以字典形式列举失败的 DU ID 及原因。
    """

    if not du_ids:
        return {
            "status": "fail",
            "errmessage": "DU ID 列表为空",
            "success_count": 0,
            "failure_count": 0,
            "success_duids": [],
            "failure_details": {},
        }

    normalized_ids: List[str] = []
    failure_details: dict[str, str] = {}
    seen_ids: set[str] = set()

    def add_failure(duid: str, reason: str) -> None:
        failure_details[duid] = reason

    for raw_id in du_ids:
        normalized = normalize_du(raw_id)
        if not normalized or not DU_RE.fullmatch(normalized):
            base_key = raw_id if isinstance(raw_id, str) and raw_id else "<empty>"
            failure_key = str(base_key) if base_key is not None else "<empty>"
            add_failure(failure_key, "无效的 DU ID")
            continue
        if normalized in seen_ids:
            add_failure(normalized, "请求中重复")
            continue
        seen_ids.add(normalized)
        normalized_ids.append(normalized)

    existing_du_ids = get_existing_du_ids(db, normalized_ids)
    success_duids: List[str] = []

    for du_id in normalized_ids:
        if du_id in existing_du_ids:
            add_failure(du_id, "DU ID 已存在")
            continue

        ensure_du(db, du_id)
        add_record(db, du_id=du_id, status="NO STATUS", remark=None, photo_url=None, lng=None, lat=None)
        success_duids.append(du_id)

    status = "ok" if success_duids else "fail"

    response = {
        "status": status,
        "success_count": len(success_duids),
        "failure_count": len(failure_details),
        "success_duids": success_duids,
        "failure_details": failure_details,
    }

    return response

# ====== 多条件（单 DU 或条件）查询（原有） ======
@app.get("/api/du/search")
def search_du_recordss(
    du_id: Optional[str] = Query(None, description="精确 DU ID"),
    status: Optional[str] = Query(None, description=f"状态过滤，可选: {VALID_STATUS_DESCRIPTION}"),
    remark: Optional[str] = Query(None, description="备注关键词(模糊)"),
    has_photo: Optional[bool] = Query(None, description="是否必须带附件 true/false"),
    date_from: Optional[datetime] = Query(None, description="起始时间(ISO 8601)"),
    date_to: Optional[datetime] = Query(None, description="结束时间(ISO 8601)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if du_id:
        du_id = normalize_du(du_id)
        if not DU_RE.fullmatch(du_id):
            raise HTTPException(status_code=400, detail=f"Invalid DU ID:{du_id}")

    total, items = search_records(
        db,
        du_id=du_id,
        status=status,
        remark_keyword=remark,
        has_photo=has_photo,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": it.id,
                "du_id": it.du_id,
                "status": it.status,
                "remark": it.remark,
                "photo_url": it.photo_url,
                "lng": it.lng if it.lng else None,
                "lat": it.lat if it.lat else None,
                "created_at": it.created_at.isoformat() if it.created_at else None,
            }
            for it in items
        ],
    }

# ====== 批量查询（新）：支持重复 du_id 参数与逗号分隔 ======
@app.get("/api/du/batch")
def batch_get_du_records(
    du_id: Optional[List[str]] = Query(None, description="重复 du_id 或逗号分隔"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    raw_ids = du_id or []
    flat: list[str] = []
    for v in raw_ids:
        for x in v.split(","):
            x = normalize_du(x)
            if x: flat.append(x)

    # 去重与过滤空值
    flat = [x for x in dict.fromkeys(flat) if x]

    if not flat:
        raise HTTPException(status_code=400, detail="Missing du_id")

    invalid = [x for x in flat if not DU_RE.fullmatch(x)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid DU ID(s): {', '.join(invalid)}")

    total, items = list_records_by_du_ids(db, flat, page=page, page_size=page_size)
    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": it.id,
                "du_id": it.du_id,
                "status": it.status,
                "remark": it.remark,
                "photo_url": it.photo_url,
                "lng": it.lng,
                "lat": it.lat,
                "created_at": it.created_at.isoformat() if it.created_at else None,
            }
            for it in items
        ],
    }

# ====== 编辑（新） ======
@app.put("/api/du/update/{id}")
def edit_record(
    id: int,
    # 方案A：multipart/form-data（和你前端 FormData 一致）
    status: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    photo: UploadFile | None = File(None),
    # 方案B：application/json（可选，若走 JSON 则上面三个会是 None）
    json_body: Optional[dict] = Body(None),
    db: Session = Depends(get_db),
):
    # 如果是 JSON 调用，取 JSON 里的字段
    if json_body and isinstance(json_body, dict):
        status = json_body.get("status", status)
        remark = json_body.get("remark", remark)
        # 不支持 JSON 方式传图片

    # 容错空字符串 -> None
    if status is not None and status.strip() == "":
        status = None
    if remark is not None:
        remark = remark.strip()
        if remark == "":
            remark = None
        elif len(remark) > 1000:            # 防止 DB 长度炸掉（按你的列宽调整）
            raise HTTPException(status_code=400, detail="remark too long (max 1000 chars)")

    # 状态校验（仅在用户真的传了 status 时校验）
    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    # 处理可选图片
    photo_url = None
    if photo and getattr(photo, "filename", None):
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    # 执行更新
    rec = update_record(db, rec_id=id, status=status, remark=remark, photo_url=photo_url)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")

    return {"ok": True, "id": rec.id}

# ====== 删除（新） ======
@app.delete("/api/du/update/{id}")
def remove_record(id: int, db: Session = Depends(get_db)):
    ok = delete_record(db, id)
    if not ok:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"ok": True}

# ====== 单 DU 历史列表（原有） ======
@app.get("/api/du/{du_id}")
def get_du_records(du_id: str, db: Session = Depends(get_db)):
    du_id = normalize_du(du_id)
    if not DU_RE.fullmatch(du_id):
        raise HTTPException(status_code=400, detail="Invalid DU ID")
    items = list_records(db, du_id)
    return {"ok": True, "items": [
        {
            "id": it.id,
            "du_id": it.du_id,
            "status": it.status,
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
            "created_at": it.created_at.isoformat() if it.created_at else None,
        } for it in items
    ]}


# ====== DN 接口 ======
@app.post("/api/dn/update")
def update_dn(
    dnNumber: str = Form(...),
    status: str = Form(...),
    remark: str | None = Form(None),
    duId: str | None = Form(None),
    photo: UploadFile | None = File(None),
    lng: str | float | None = Form(None),
    lat: str | float | None = Form(None),
    db: Session = Depends(get_db),
):
    dn_number = normalize_dn(dnNumber)
    if not DN_RE.fullmatch(dn_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    du_id_normalized = None
    if duId:
        du_id_normalized = normalize_du(duId)
        if not DU_RE.fullmatch(du_id_normalized):
            raise HTTPException(status_code=400, detail="Invalid DU ID")

    photo_url = None
    if photo and photo.filename:
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    lng_val = str(lng) if lng else None
    lat_val = str(lat) if lat else None

    ensure_dn(
        db,
        dn_number,
        du_id=du_id_normalized,
        status=status,
        remark=remark,
        photo_url=photo_url,
        lng=lng_val,
        lat=lat_val,
    )
    if du_id_normalized:
        ensure_du(db, du_id_normalized)

    rec = add_dn_record(
        db,
        dn_number=dn_number,
        status=status,
        remark=remark,
        photo_url=photo_url,
        lng=lng_val,
        lat=lat_val,
        du_id=du_id_normalized,
    )
    return {"ok": True, "id": rec.id, "photo": photo_url}


@app.post("/api/dn/batch_update")
def batch_update_dn(
    dn_numbers: List[str] = Body(..., description="JSON array of DN numbers to create"),
    db: Session = Depends(get_db),
):
    if not dn_numbers:
        return {
            "status": "fail",
            "errmessage": "DN number 列表为空",
            "success_count": 0,
            "failure_count": 0,
            "success_dn_numbers": [],
            "failure_details": {},
        }

    normalized_numbers: List[str] = []
    failure_details: dict[str, str] = {}
    seen_numbers: set[str] = set()

    def add_failure(number: str, reason: str) -> None:
        failure_details[number] = reason

    for raw_number in dn_numbers:
        normalized = normalize_dn(raw_number)
        if not normalized or not DN_RE.fullmatch(normalized):
            base_key = raw_number if isinstance(raw_number, str) and raw_number else "<empty>"
            failure_key = str(base_key) if base_key is not None else "<empty>"
            add_failure(failure_key, "无效的 DN number")
            continue
        if normalized in seen_numbers:
            add_failure(normalized, "请求中重复")
            continue
        seen_numbers.add(normalized)
        normalized_numbers.append(normalized)

    existing_numbers = get_existing_dn_numbers(db, normalized_numbers)
    success_numbers: List[str] = []

    for number in normalized_numbers:
        if number in existing_numbers:
            add_failure(number, "DN number 已存在")
            continue

        ensure_dn(db, number, status="NO STATUS")
        add_dn_record(
            db,
            dn_number=number,
            status="NO STATUS",
            remark=None,
            photo_url=None,
            lng=None,
            lat=None,
        )
        success_numbers.append(number)

    status_value = "ok" if success_numbers else "fail"

    return {
        "status": status_value,
        "success_count": len(success_numbers),
        "failure_count": len(failure_details),
        "success_dn_numbers": success_numbers,
        "failure_details": failure_details,
    }


@app.get("/api/dn/search")
def search_dn_records_api(
    dn_number: Optional[str] = Query(None, description="精确 DN number"),
    du_id: Optional[str] = Query(None, description="关联 DU ID"),
    status: Optional[str] = Query(None, description=f"状态过滤，可选: {VALID_STATUS_DESCRIPTION}"),
    remark: Optional[str] = Query(None, description="备注关键词(模糊)"),
    has_photo: Optional[bool] = Query(None, description="是否必须带附件 true/false"),
    date_from: Optional[datetime] = Query(None, description="起始时间(ISO 8601)"),
    date_to: Optional[datetime] = Query(None, description="结束时间(ISO 8601)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if dn_number:
        dn_number = normalize_dn(dn_number)
        if not DN_RE.fullmatch(dn_number):
            raise HTTPException(status_code=400, detail=f"Invalid DN number: {dn_number}")
    if du_id:
        du_id = normalize_du(du_id)
        if not DU_RE.fullmatch(du_id):
            raise HTTPException(status_code=400, detail=f"Invalid DU ID: {du_id}")

    total, items = search_dn_records(
        db,
        dn_number=dn_number,
        du_id=du_id,
        status=status,
        remark_keyword=remark,
        has_photo=has_photo,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
                "status": it.status,
                "remark": it.remark,
                "photo_url": it.photo_url,
                "lng": it.lng,
                "lat": it.lat,
                "created_at": it.created_at.isoformat() if it.created_at else None,
            }
            for it in items
        ],
    }


@app.get("/api/dn/batch")
def batch_get_dn_records(
    dn_number: Optional[List[str]] = Query(None, description="重复 dn_number 或逗号分隔"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    raw_numbers = dn_number or []
    flat: list[str] = []
    for value in raw_numbers:
        for part in value.split(","):
            normalized = normalize_dn(part)
            if normalized:
                flat.append(normalized)

    flat = [x for x in dict.fromkeys(flat) if x]

    if not flat:
        raise HTTPException(status_code=400, detail="Missing dn_number")

    invalid = [x for x in flat if not DN_RE.fullmatch(x)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid DN number(s): {', '.join(invalid)}")

    total, items = list_dn_records_by_dn_numbers(db, flat, page=page, page_size=page_size)
    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
                "status": it.status,
                "remark": it.remark,
                "photo_url": it.photo_url,
                "lng": it.lng,
                "lat": it.lat,
                "created_at": it.created_at.isoformat() if it.created_at else None,
            }
            for it in items
        ],
    }


@app.put("/api/dn/update/{id}")
def edit_dn_record(
    id: int,
    status: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    duId: Optional[str] = Form(None),
    photo: UploadFile | None = File(None),
    json_body: Optional[dict] = Body(None),
    db: Session = Depends(get_db),
):
    du_id_provided = duId is not None

    if json_body and isinstance(json_body, dict):
        if "status" in json_body:
            status = json_body.get("status")
        if "remark" in json_body:
            remark = json_body.get("remark")
        if "duId" in json_body:
            duId = json_body.get("duId")
            du_id_provided = True

    if status is not None and status.strip() == "":
        status = None
    if remark is not None:
        remark = remark.strip()
        if remark == "":
            remark = None
        elif len(remark) > 1000:
            raise HTTPException(status_code=400, detail="remark too long (max 1000 chars)")

    if duId is not None:
        duId = duId.strip()
        if duId == "":
            duId = None
        else:
            normalized_candidate = normalize_du(duId)
            if not DU_RE.fullmatch(normalized_candidate):
                raise HTTPException(status_code=400, detail="Invalid DU ID")
            duId = normalized_candidate
    elif du_id_provided:
        duId = None

    if status is not None and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")

    photo_url = None
    if photo and getattr(photo, "filename", None):
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    normalized_du_id = duId if duId else None
    if normalized_du_id:
        ensure_du(db, normalized_du_id)

    rec = update_dn_record(
        db,
        rec_id=id,
        status=status,
        remark=remark,
        photo_url=photo_url,
        du_id=normalized_du_id,
        du_id_set=du_id_provided,
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")

    ensure_dn(
        db,
        rec.dn_number,
        du_id=rec.du_id,
        status=rec.status,
        remark=rec.remark,
        photo_url=rec.photo_url,
        lng=rec.lng,
        lat=rec.lat,
    )

    return {"ok": True, "id": rec.id}


@app.delete("/api/dn/update/{id}")
def remove_dn_record(id: int, db: Session = Depends(get_db)):
    ok = delete_dn_record(db, id)
    if not ok:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"ok": True}


# 设置全局变量
API_KEY = "AIzaSyCxIBYFpNlPvQUXY83S559PEVXoagh8f88"
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/13-D-KkkbilYmlcHHa__CZkE2xtynL--ZxekZG4lWRic/edit?gid=1258103322#gid=1258103322"

SHEET_COLUMNS: List[str] = [
    "dn_number",
    "du_id",
    "status_wh",
    "lsp",
    "area",
    "mos_given_time",
    "expected_arrival_time_from_project",
    "project_request",
    "distance_poll_mover_to_site",
    "driver_contact_name",
    "driver_contact_number",
    "delivery_type_a_to_b",
    "transportation_time",
    "estimate_depart_from_start_point_etd",
    "estimate_arrive_sites_time_eta",
    "lsp_tracker",
    "hw_tracker",
    "actual_depart_from_start_point_atd",
    "actual_arrive_time_ata",
    "subcon",
    "subcon_receiver_contact_number",
    "status_delivery",
    "issue_remark",
    "mos_attempt_1st_time",
    "mos_attempt_2nd_time",
    "mos_attempt_3rd_time",
    "mos_attempt_4th_time",
    "mos_attempt_5th_time",
    "mos_attempt_6th_time",
    "mos_type",
    "region",
    "plan_mos_date",
]

MONTH_MAP = {
    "Sept": "Sep",  # 'Sept' -> 'Sep'
}

DATE_FORMATS = [
    "%d %b %y", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%d%b", "%d %b %y", "%d %b %Y"
]

def fetch_plan_sheets(sheet_url):
    """获取以 'Plan MOS' 开头的所有工作表"""
    sheets = sheet_url.worksheets()
    return [sheet for sheet in sheets if sheet.title.startswith("Plan MOS")]

def parse_date(date_str: str):
    """解析日期字符串"""
    current_year = datetime.now().year
    
    # 替换月份简写
    for incorrect, correct in MONTH_MAP.items():
        date_str = date_str.replace(incorrect, correct)

    # 处理日期字符串
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue

    return date_str

def process_sheet_data(sheet) -> pd.DataFrame:
    """处理工作表数据"""
    data = sheet.get_all_values()[3:]  # 从第4行开始
    trimmed: List[List[str]] = []
    column_count = len(SHEET_COLUMNS)
    for row in data:
        row_values = row[:column_count]
        if len(row_values) < column_count:
            row_values = row_values + [""] * (column_count - len(row_values))
        trimmed.append(row_values)

    df = pd.DataFrame(trimmed, columns=SHEET_COLUMNS)
    return df

def process_all_sheets(sh) -> pd.DataFrame:
    """处理所有符合条件的工作表并合并数据"""
    plan_sheets = fetch_plan_sheets(sh)
    all_data = [process_sheet_data(sheet) for sheet in plan_sheets]
    if not all_data:
        return pd.DataFrame(columns=SHEET_COLUMNS)
    return pd.concat(all_data, ignore_index=True)


def normalize_sheet_value(value: Any) -> Any:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if pd.isna(value):
        return None
    return value


def sync_dn_sheet_to_db(db: Session, *, logger_obj: logging.Logger | None = None) -> List[str]:
    """同步 Google Sheet 中的 DN 数据到数据库。

    返回成功同步的 DN number 列表（按字典序升序）。
    """
    log = logger_obj or logger

    try:
        gc = gspread.api_key(API_KEY)
        sh = gc.open_by_url(SPREADSHEET_URL)
        combined_df = process_all_sheets(sh)
    except Exception as exc:
        if log:
            log.exception("Failed to fetch DN sheet data: %s", exc)
        raise

    records: List[dict[str, Any]] = []
    dn_numbers: set[str] = set()

    if not combined_df.empty:
        for record in combined_df.to_dict(orient="records"):
            cleaned = {key: normalize_sheet_value(value) for key, value in record.items()}
            raw_number = cleaned.get("dn_number")
            raw_number_str = str(raw_number).strip() if raw_number is not None else ""
            normalized_number = normalize_dn(raw_number_str) if raw_number_str else ""
            if not normalized_number:
                continue
            cleaned["dn_number"] = normalized_number
            if all(value is None for key, value in cleaned.items() if key != "dn_number"):
                continue
            records.append(cleaned)
            dn_numbers.add(normalized_number)

    if not dn_numbers:
        return []

    latest_records_for_update = get_latest_dn_records_map(db, dn_numbers)

    for entry in records:
        number = entry["dn_number"]
        sheet_fields = {key: entry.get(key) for key in SHEET_COLUMNS if key != "dn_number"}
        latest = latest_records_for_update.get(number)
        if latest:
            if not sheet_fields.get("du_id") and latest.du_id:
                sheet_fields["du_id"] = latest.du_id
            sheet_fields.update(
                {
                    "status": latest.status,
                    "remark": latest.remark,
                    "photo_url": latest.photo_url,
                    "lng": latest.lng,
                    "lat": latest.lat,
                }
            )
        ensure_dn(db, number, **sheet_fields)

    return sorted(dn_numbers)


def _sync_dn_sheet_with_new_session() -> List[str]:
    db = SessionLocal()
    try:
        try:
            synced_numbers = sync_dn_sheet_to_db(db, logger_obj=logger)
        except Exception as exc:
            create_dn_sync_log(
                db,
                status="failed",
                synced_numbers=None,
                message="Failed to sync DN data from Google Sheet",
                error_message=str(exc),
                error_traceback=traceback.format_exc(),
            )
            raise
        else:
            message = (
                "Synced %d DN numbers from Google Sheet" % len(synced_numbers)
                if synced_numbers
                else "Google Sheet returned no DN rows to sync"
            )
            create_dn_sync_log(
                db,
                status="success",
                synced_numbers=synced_numbers,
                message=message,
            )
            return synced_numbers
    finally:
        db.close()


async def run_dn_sheet_sync_once() -> List[str]:
    return await asyncio.to_thread(_sync_dn_sheet_with_new_session)


async def _scheduled_dn_sheet_sync() -> None:
    try:
        synced_numbers = await run_dn_sheet_sync_once()
        if synced_numbers:
            logger.info("Synced %d DN numbers from Google Sheet", len(synced_numbers))
    except Exception:
        logger.exception("Scheduled DN sheet sync failed")


@app.post("/api/dn/sync")
async def trigger_dn_sync():
    try:
        synced_numbers = await run_dn_sheet_sync_once()
    except Exception:
        logger.exception("Manual DN sheet sync failed")
        raise HTTPException(status_code=500, detail="dn_sync_failed") from None

    return {
        "ok": True,
        "synced_count": len(synced_numbers),
        "dn_numbers": synced_numbers,
    }


@app.get("/api/dn/sync/log/latest")
def get_latest_dn_sync_log_entry(db: Session = Depends(get_db)):
    log_entry = get_latest_dn_sync_log(db)
    if not log_entry:
        return {"ok": True, "data": None}

    return {
        "ok": True,
        "data": {
            "id": log_entry.id,
            "status": log_entry.status,
            "synced_count": log_entry.synced_count,
            "dn_numbers": log_entry.dn_numbers,
            "message": log_entry.message,
            "error_message": log_entry.error_message,
            "error_traceback": log_entry.error_traceback,
            "created_at": log_entry.created_at.isoformat() if log_entry.created_at else None,
        },
    }


@app.on_event("startup")
async def _start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _scheduled_dn_sheet_sync,
        trigger=IntervalTrigger(seconds=SHEET_SYNC_INTERVAL_SECONDS),
        id=_SHEET_SYNC_JOB_ID,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.utcnow() + timedelta(seconds=5),
    )
    _scheduler.start()


@app.on_event("shutdown")
async def _shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


@app.get("/api/dn/stats/{date}")
async def get_dn_stats(date: str):
    # 初始化Google Sheets客户端
    gc = gspread.api_key(API_KEY)
    sh = gc.open_by_url(SPREADSHEET_URL)

    # 获取所有工作表数据
    combined_df = process_all_sheets(sh)

    # 处理日期列
    combined_df["plan_mos_date"] = combined_df["plan_mos_date"].apply(
        lambda x: parse_date(x).strftime("%d-%b-%y") if parse_date(x) else x
    )

    # 筛选出特定日期的数据
    day_df = combined_df[combined_df["plan_mos_date"] == date]
    day_df["status_delivery"] = day_df["status_delivery"].apply(lambda x: x.upper() if x else "NO STATUS")

    # 创建透视表
    pivot_df = day_df.groupby(["plan_mos_date", "region", "status_delivery"])["dn_number"].nunique().unstack(fill_value=0)

    # 所有可能的状态值
    all_statuses = [
        "PREPARE VEHICLE", "ON THE WAY", "ON SITE", "POD", "REPLAN MOS PROJECT", "WAITING PIC FEEDBACK", 
        "REPLAN MOS DUE TO LSP DELAY", "CLOSE BY RN", "CANCEL MOS", "NO STATUS"
    ]

    # 补充状态值
    extra = list(set(pivot_df.columns.tolist()) - set(all_statuses))
    final_statuses = all_statuses + extra

    # 重新索引并添加总计列
    pivot_df = pivot_df.reindex(columns=final_statuses, fill_value=0)
    pivot_df["Total"] = pivot_df.sum(axis=1)

    # 转换为最终表格格式
    table_df = pivot_df.reset_index()
    table_df.columns = ["date", "group"] + table_df.columns.to_list()[2:]

    # 转换为所需的格式
    raw_rows = [
        {
            'group': row['group'],
            'date': row['date'],
            'values': list(row)[2:]
        }
        for _, row in table_df.iterrows()
    ]

    return {"ok": True, "data": raw_rows}


@app.get("/api/dn/list")
async def get_dn_list(db: Session = Depends(get_db)):
    items = db.query(DN).order_by(DN.dn_number.asc()).all()

    if not items:
        return {"ok": True, "data": []}

    latest_records = get_latest_dn_records_map(db, [it.dn_number for it in items])

    data: List[dict[str, Any]] = []
    for it in items:
        row: dict[str, Any] = {
            "id": it.id,
            "dn_number": it.dn_number,
            "created_at": it.created_at.isoformat() if it.created_at else None,
            "status": it.status,
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
        }
        for column in SHEET_COLUMNS:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = (
            latest.created_at.isoformat() if latest and latest.created_at else None
        )
        data.append(row)

    return {"ok": True, "data": data}


@app.get("/api/dn/list/search")
def search_dn_list_api(
    date: str | None = Query(None, description="Plan MOS date"),
    dnnumber: str | None = Query(None, description="DN number"),
    status: str | None = Query(None, description="Status delivery"),
    lsp: str | None = Query(None, description="LSP"),
    region: str | None = Query(None, description="Region"),
    project: str | None = Query(None, description="Project request"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    plan_date = date.strip() if date else None
    dn_number = normalize_dn(dnnumber) if dnnumber else None
    if dn_number and not DN_RE.fullmatch(dn_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    total, items = search_dn_list(
        db,
        plan_mos_date=plan_date,
        dn_number=dn_number,
        status_delivery=status.strip() if status else None,
        lsp=lsp.strip() if lsp else None,
        region=region.strip() if region else None,
        project_request=project.strip() if project else None,
        page=page,
        page_size=page_size,
    )

    if not items:
        return {
            "ok": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [],
        }

    latest_records = get_latest_dn_records_map(db, [it.dn_number for it in items])

    data: list[dict[str, Any]] = []
    for it in items:
        row: dict[str, Any] = {
            "id": it.id,
            "dn_number": it.dn_number,
            "created_at": it.created_at.isoformat() if it.created_at else None,
            "status": it.status,
            "remark": it.remark,
            "photo_url": it.photo_url,
            "lng": it.lng,
            "lat": it.lat,
        }
        for column in SHEET_COLUMNS:
            if column == "dn_number":
                continue
            row[column] = getattr(it, column)
        latest = latest_records.get(it.dn_number)
        row["latest_record_created_at"] = (
            latest.created_at.isoformat() if latest and latest.created_at else None
        )
        data.append(row)

    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": data,
    }


@app.get("/api/dn/{dn_number}")
def get_dn_records(dn_number: str, db: Session = Depends(get_db)):
    dn_number = normalize_dn(dn_number)
    if not DN_RE.fullmatch(dn_number):
        raise HTTPException(status_code=400, detail="Invalid DN number")

    items = list_dn_records(db, dn_number)
    return {
        "ok": True,
        "items": [
            {
                "id": it.id,
                "dn_number": it.dn_number,
                "du_id": it.du_id,
                "status": it.status,
                "remark": it.remark,
                "photo_url": it.photo_url,
                "lng": it.lng,
                "lat": it.lat,
                "created_at": it.created_at.isoformat() if it.created_at else None,
            }
            for it in items
        ],
    }


# 可选：支持 python -m app.main 本地跑，避免相对导入报错
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=10000, reload=True)