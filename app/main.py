# app/main.py
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional, List
from datetime import datetime
import re, os, unicodedata

from .settings import settings
from .db import Base, engine, get_db
from .crud import (
    ensure_du, add_record, list_records, search_records,
    list_records_by_du_ids, update_record, delete_record,
)
from .storage import save_file
from fastapi.responses import JSONResponse
import logging, traceback
import gspread
import pandas as pd

# ====== 启动与静态 ======
os.makedirs(settings.storage_disk_path, exist_ok=True)
app = FastAPI(title="DU Backend API", version="1.1.0")

logger = logging.getLogger("uvicorn.error")

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
    if status not in ("运输中", "过夜", "已到达"):
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

# ====== 多条件（单 DU 或条件）查询（原有） ======
@app.get("/api/du/search")
def search_du_recordss(
    du_id: Optional[str] = Query(None, description="精确 DU ID"),
    status: Optional[str] = Query(None, description="运输中/过夜/已到达"),
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
from typing import Optional
from fastapi import Body

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
    if status is not None and status not in ("运输中", "过夜", "已到达"):
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


# 设置全局变量
API_KEY = "AIzaSyCxIBYFpNlPvQUXY83S559PEVXoagh8f88"
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/13-D-KkkbilYmlcHHa__CZkE2xtynL--ZxekZG4lWRic/edit?gid=1258103322#gid=1258103322"
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
    data = [row[:32] for row in data]  # 取前32列
    df = pd.DataFrame(
        data,
        columns=[
            "dn_number", "du_id", "status_wh", "lsp", "area", "mos_given_time", "expected_arrival_time_from_project", 
            "project_request", "distance_poll_mover_to_site", "driver_contact_name", "driver_contact_number", 
            "delivery_type_a_to_b", "transportation_time", "estimate_depart_from_start_point_etd", 
            "estimate_arrive_sites_time_eta", "lsp_tracker", "hw_tracker", "actual_depart_from_start_point_atd", 
            "actual_arrive_time_ata", "subcon", "subcon_receiver_contact_number", "status_delivery", "issue_remark", 
            "mos_attempt_1st_time", "mos_attempt_2nd_time", "mos_attempt_3rd_time", "mos_attempt_4th_time", 
            "mos_attempt_5th_time", "mos_attempt_6th_time", "mos_type", "region", "plan_mos_date",
        ]
    )
    return df

def process_all_sheets(sh) -> pd.DataFrame:
    """处理所有符合条件的工作表并合并数据"""
    plan_sheets = fetch_plan_sheets(sh)
    all_data = [process_sheet_data(sheet) for sheet in plan_sheets]
    return pd.concat(all_data, ignore_index=True)

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


# 可选：支持 python -m app.main 本地跑，避免相对导入报错
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=10000, reload=True)