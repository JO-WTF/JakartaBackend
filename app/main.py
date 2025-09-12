from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Query, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime
import re
import os

from .settings import settings
from .db import Base, engine, get_db
from .storage import save_file

# 现有 CRUD
from .crud import ensure_du, add_update, list_updates, search_updates
# 新增的 CRUD（你按我给的完整 crud.py 替换后就有了这些）
from .crud import get_update_by_id, update_update, delete_update, list_updates_by_du_ids

os.makedirs(settings.storage_disk_path, exist_ok=True)

app = FastAPI(title="DU Backend API", version="1.1.0")

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

DU_RE = re.compile(r"^DID\d{13}$")
VALID_STATUS = ("运输中", "过夜", "已到达")

@app.get("/")
def healthz():
    return {"ok": True, "message": "You can use admin panel now."}

# -------------------------
# 已有：创建记录
# -------------------------
@app.post("/api/du/update")
def update_du(
    duId: str = Form(...),
    status: str = Form(...),
    remark: str | None = Form(None),
    photo: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    if not DU_RE.fullmatch(duId):
        raise HTTPException(status_code=400, detail="Invalid DU ID")
    if status not in VALID_STATUS:
        raise HTTPException(status_code=400, detail="Invalid status")

    ensure_du(db, duId)

    photo_url = None
    if photo and photo.filename:
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    rec = add_update(db, du_id=duId, status=status, remark=remark, photo_url=photo_url)
    return {"ok": True, "id": rec.id, "photo": photo_url}

# -------------------------
# 已有：条件检索
# -------------------------
@app.get("/api/du/search")
def search_du_updates(
    du_id: Optional[str] = Query(None, description="精确 DU ID (13位数字)"),
    status: Optional[str] = Query(None, description="运输中/过夜/已到达"),
    remark: Optional[str] = Query(None, description="备注关键词(模糊)"),
    has_photo: Optional[bool] = Query(None, description="是否必须带附件 true/false"),
    date_from: Optional[datetime] = Query(None, description="起始时间(ISO 8601)"),
    date_to: Optional[datetime] = Query(None, description="结束时间(ISO 8601)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if du_id and not DU_RE.fullmatch(du_id):
        raise HTTPException(status_code=400, detail=f"Invalid DU ID:{du_id}")

    total, items = search_updates(
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
                "created_at": it.created_at.isoformat() if it.created_at else None,
            }
            for it in items
        ],
    }

# -------------------------
# 已有：单 DU 全量列表
# -------------------------
@app.get("/api/du/{du_id}")
def get_du_updates(du_id: str, db: Session = Depends(get_db)):
    if not DU_RE.fullmatch(du_id):
        raise HTTPException(status_code=400, detail="Invalid DU ID")
    items = list_updates(db, du_id)
    return {"ok": True, "items": [
        {
            "id": it.id,
            "du_id": it.du_id,
            "status": it.status,
            "remark": it.remark,
            "photo_url": it.photo_url,
            "created_at": it.created_at.isoformat() if it.created_at else None,
        } for it in items
    ]}

# -------------------------
# 新增：修改记录
# -------------------------
@app.put("/api/du/update/{rec_id}")
def modify_update(
    rec_id: int = Path(..., ge=1),
    status: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    photo: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    obj = get_update_by_id(db, rec_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Record not found")

    if status is not None and status not in VALID_STATUS:
        raise HTTPException(status_code=400, detail="Invalid status")

    photo_url = None
    if photo and photo.filename:
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    obj = update_update(db, rec_id, status=status, remark=remark, photo_url=photo_url)
    return {
        "ok": True,
        "item": {
            "id": obj.id,
            "du_id": obj.du_id,
            "status": obj.status,
            "remark": obj.remark,
            "photo_url": obj.photo_url,
            "created_at": obj.created_at.isoformat() if obj.created_at else None,
        },
    }

# -------------------------
# 新增：删除记录
# -------------------------
@app.delete("/api/du/update/{rec_id}")
def remove_update(
    rec_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    obj = get_update_by_id(db, rec_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Record not found")
    ok = delete_update(db, rec_id)
    return {"ok": ok, "deleted_id": rec_id}

# -------------------------
# 新增：批量查询多个 DU
# -------------------------
@app.get("/api/du/batch")
def batch_get_du_updates(
    du_id: Optional[list[str]] = Query(None, description="可重复传多个 du_id；也支持逗号分隔"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    # 支持 ?du_id=A&du_id=B 以及 ?du_id=A,B
    du_ids: list[str] = []
    if du_id:
        for v in du_id:
            du_ids.extend([x.strip() for x in v.split(",") if x.strip()])

    # 校验
    for _id in du_ids:
        if not DU_RE.fullmatch(_id):
            raise HTTPException(status_code=400, detail=f"Invalid DU ID: {_id}")

    total, items = list_updates_by_du_ids(db, du_ids, page=page, page_size=page_size)
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
                "created_at": it.created_at.isoformat() if it.created_at else None,
            }
            for it in items
        ],
    }
