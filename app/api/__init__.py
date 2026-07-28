"""Application API routers."""

from fastapi import APIRouter

from .health import router as health_router
from .dn import router as dn_router
from .find_sg_lpn import router as find_sg_lpn_router
from .vehicle import router as vehicle_router
from .pm import router as pm_router
from .aging_orders import router as aging_orders_router

router = APIRouter()
router.include_router(health_router)
router.include_router(find_sg_lpn_router)
router.include_router(dn_router)
router.include_router(vehicle_router)
router.include_router(pm_router)
router.include_router(aging_orders_router)

__all__ = ["router"]
