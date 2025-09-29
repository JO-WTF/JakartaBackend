"""Health endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def healthz():
    return {"ok": True, "message": "You can use admin panel now."}
