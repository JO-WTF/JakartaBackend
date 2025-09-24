"""FastAPI 应用入口，负责装配路由与基础设施。"""

from __future__ import annotations

import logging
import os
import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import Base, engine
from .dn_columns import refresh_dynamic_columns
from .routers import dn as dn_router
from .routers import sync as sync_router
from .settings import settings
from .tasks.dn_sync_scheduler import shutdown_scheduler, start_scheduler

logger = logging.getLogger("uvicorn.error")

if settings.storage_driver != "s3":
    os.makedirs(settings.storage_disk_path, exist_ok=True)

Base.metadata.create_all(bind=engine)
refresh_dynamic_columns(engine)

app = FastAPI(title="DU Backend API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.storage_driver != "s3":
    app.mount(
        "/uploads",
        StaticFiles(directory=settings.storage_disk_path, check_dir=False),
        name="uploads",
    )


@app.on_event("startup")
async def on_startup() -> None:
    """启动应用时开启后台同步调度。"""

    start_scheduler()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """应用关闭时停止同步调度。"""

    shutdown_scheduler()


@app.exception_handler(Exception)
async def all_exception_handler(request: Request, exc: Exception):
    """捕获所有未处理异常并输出统一的错误响应。"""

    logger.error(
        "Unhandled error on %s %s\n%s",
        request.method,
        request.url.path,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "internal_error",
        },
    )


@app.get("/")
def healthz() -> dict[str, Any]:
    """提供健康检查与欢迎信息。"""

    return {"ok": True, "message": "You can use admin panel now."}


app.include_router(dn_router.router)
app.include_router(sync_router.router)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=10000, reload=True)
