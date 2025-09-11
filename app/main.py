from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from .settings import settings
from .db import Base, engine, get_db
from .crud import ensure_du, add_update, list_updates
from .storage import save_file
import re
import os
from fastapi import Query
from typing import Optional
from datetime import datetime
from .crud import search_updates

os.makedirs(settings.storage_disk_path, exist_ok=True)

app = FastAPI(title="DU Backend API", version="1.0.0")

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

DU_RE = re.compile(r"^\d{10,20}$")

@app.get("/healthz")
def healthz():
    return {"ok": True}

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
    if status not in ("运输中", "已到达"):
        raise HTTPException(status_code=400, detail="Invalid status")

    ensure_du(db, duId)

    photo_url = None
    if photo and photo.filename:
        content = photo.file.read()
        photo_url = save_file(content, photo.content_type or "application/octet-stream")

    rec = add_update(db, du_id=duId, status=status, remark=remark, photo_url=photo_url)
    return {"ok": True, "id": rec.id, "photo": photo_url}

@app.get("/api/du/search")
def search_du_updates(
    du_id: Optional[str] = Query(None, description="精确 DU ID (10-20 位数字)"),
    status: Optional[str] = Query(None, description="运输中/已到达"),
    remark: Optional[str] = Query(None, description="备注关键词(模糊)"),
    has_photo: Optional[bool] = Query(None, description="是否必须带附件 true/false"),
    date_from: Optional[datetime] = Query(None, description="起始时间(ISO 8601)"),
    date_to: Optional[datetime] = Query(None, description="结束时间(ISO 8601)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if du_id and not re.fullmatch(r"^\d{10,20}$", du_id):
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
