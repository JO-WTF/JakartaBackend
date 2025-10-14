from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import ValidationError


def validate_body(schema: type):
    """Return a dependency that validates request body against the provided Pydantic schema."""

    async def _validator(data: dict = Depends()):
        try:
            return schema(**data)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors())

    return _validator
