from fastapi import APIRouter

from .inventory import router as pm_inventory_router

router = APIRouter(prefix="/api/pm")
router.include_router(pm_inventory_router)

__all__ = ["router"]
