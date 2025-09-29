"""DN API routers."""

from fastapi import APIRouter

from .archive import router as archive_router
from .columns import router as columns_router
from .list import router as list_router
from .query import router as query_router
from .stats import router as stats_router
from .sync import router as sync_router
from .update import router as update_router

router = APIRouter()
router.include_router(columns_router)
router.include_router(archive_router)
router.include_router(update_router)
router.include_router(sync_router)
router.include_router(stats_router)
router.include_router(list_router)
router.include_router(query_router)

__all__ = ["router"]
