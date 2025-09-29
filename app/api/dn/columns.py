"""DN column management endpoints."""

from fastapi import Depends, HTTPException, APIRouter
from sqlalchemy.orm import Session

from app.dn_columns import extend_dn_columns as extend_dn_table_columns, get_sheet_columns
from app.db import get_db
from app.schemas.dn import DNColumnExtensionRequest

router = APIRouter(prefix="/api/dn/columns")


@router.post("/extend")
def extend_dn_columns_api(request: DNColumnExtensionRequest, db: Session = Depends(get_db)):
    try:
        added = extend_dn_table_columns(db, request.columns)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"ok": True, "added_columns": added, "columns": get_sheet_columns()}
