"""Vehicle API routers."""

from fastapi import APIRouter

from .depart import router as depart_router
from .query import router as query_router
from .signin import router as signin_router

router = APIRouter()
router.include_router(signin_router)
router.include_router(query_router)
router.include_router(depart_router)

__all__ = ["router"]
