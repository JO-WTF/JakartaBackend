"""Shared router instance for DN endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/dn", tags=["DN"])

__all__ = ["router"]
