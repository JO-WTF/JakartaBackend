"""API routers for the FastAPI application."""

from fastapi import FastAPI

from . import dn, root, vehicle


def register_routes(app: FastAPI) -> None:
    """Register all API routers with the provided FastAPI application."""

    app.include_router(root.router)
    app.include_router(dn.router)
    app.include_router(vehicle.router)
