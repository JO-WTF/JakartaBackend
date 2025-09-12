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
    ensure_du, add_update, list_updates, search_updates,
    list_updates_by_du_ids, update_update, delete_update,
)
from .storage import save_file

# ====== 启动与静态 ======
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

# ====== 校验与清洗 ======
DU_RE = re.compile(r"^DID\d{13}$")

def normalize_du(s: str) -> str:
    """NFC 规整、去零宽、全角转半角、去空白、统一大写"""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    s = s.replace("\u200b", "").replace("\ufeff", "")
    s = s.strip().upper()
    trans = str.maketrans("０１２３４５６７８９", "0123456789")
    s = s.translate(trans)
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

    rec = add_update(db, du_id=duId, status=status, remark=remark, photo_url=photo_url)
    return {"ok": True, "id": rec.id, "photo": photo_url}

# ====== 多条件（单 DU 或条件）查询（原有） ======
@app.get("/api/du/search")
def search_du_updates(
    du_id: Optional[str] = Query(None, description="精确 DU ID (DID+13位数字)"),
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

# ====== 批量查询（新）：支持重复 du_id 参数与逗号分隔 ======
@app.get("/api/du/batch")
def batch_get_du_updates(
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

    # 去重与过滤占位“DID”
    flat = [x for x in dict.fromkeys(flat) if x != "DID"]

    if not flat:
        raise HTTPException(status_code=400, detail="Missing du_id")

    invalid = [x for x in flat if not DU_RE.fullmatch(x)]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Invalid DU ID(s): {', '.join(invalid)}")

    total, items = list_updates_by_du_ids(db, flat, page=page, page_size=page_size)
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

# ====== 编辑（新） ======
@app.put("/api/du/update/{id}")
def edit_update(
    id: int,
    status: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    photo: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    if status and status not in ("运输中", "过夜", "已到达"):
        raise HTTPException(status_code=400, detail="Invalid status")
    photo_url = None
    if photo and photo.filename:
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")
    rec = update_update(db, id=id, status=status, remark=remark, photo_url=photo_url)
    if not rec:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"ok": True, "id": rec.id}

# ====== 删除（新） ======
@app.delete("/api/du/update/{id}")
def remove_update(id: int, db: Session = Depends(get_db)):
    ok = delete_update(db, id)
    if not ok:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"ok": True}

# ====== 单 DU 历史列表（原有） ======
@app.get("/api/du/{du_id}")
def get_du_updates(du_id: str, db: Session = Depends(get_db)):
    du_id = normalize_du(du_id)
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

# 可选：支持 python -m app.main 本地跑，避免相对导入报错
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=10000, reload=True)
