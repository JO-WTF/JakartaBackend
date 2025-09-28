"""Root level endpoints."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def healthz() -> dict[str, object]:
    """Health check endpoint used by monitoring systems."""

    return {"ok": True, "message": "You can use admin panel now."}
