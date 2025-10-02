from __future__ import annotations

import re
from typing import Iterable, List, Mapping, Collection

from sqlalchemy import Column, Text as SAText, inspect as sa_inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .db import engine
from .models import DN
from app.utils.logging import logger

# Base columns defined on the SQLAlchemy model when the application starts.
_BASE_DN_COLUMNS = tuple(column.name for column in DN.__table__.columns)
_BASE_DN_COLUMN_SET = set(_BASE_DN_COLUMNS)
# Columns that should never be updated through sheet synchronization.
_IMMUTABLE_COLUMNS = {"id", "dn_number", "created_at"}

# Base sheet columns that mirror the Google Sheet structure.
SHEET_BASE_COLUMNS: List[str] = [
    "dn_number",
    "du_id",
    "status_wh",
    "lsp",
    "area",
    "mos_given_time",
    "expected_arrival_time_from_project",
    "project_request",
    "distance_poll_mover_to_site",
    "driver_contact_name",
    "driver_contact_number",
    "delivery_type_a_to_b",
    "transportation_time",
    "estimate_depart_from_start_point_etd",
    "estimate_arrive_sites_time_eta",
    "lsp_tracker",
    "hw_tracker",
    "actual_depart_from_start_point_atd",
    "actual_arrive_time_ata",
    "subcon",
    "subcon_receiver_contact_number",
    "status_delivery",
    "issue_remark",
    "mos_attempt_1st_time",
    "mos_attempt_2nd_time",
    "mos_attempt_3rd_time",
    "mos_attempt_4th_time",
    "mos_attempt_5th_time",
    "mos_attempt_6th_time",
    "mos_type",
    "region",
    "plan_mos_date",
]

# Cache of dynamically added DN columns (in table order).
_dynamic_columns: List[str] = []

_COLUMN_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _get_engine(bind: Engine | Session | None = None) -> Engine:
    if isinstance(bind, Session):
        if bind.bind is None:
            raise RuntimeError("Session is not bound to an engine")
        return bind.bind
    if isinstance(bind, Engine):
        return bind
    return engine


def _register_column_on_model(column_name: str) -> None:
    """Attach a dynamic column to the SQLAlchemy model for ORM access."""

    table = DN.__table__
    if column_name in table.c:
        return

    logger.debug("Registering dynamic DN column on model: %s", column_name)
    column = Column(column_name, SAText, nullable=True)
    table.append_column(column)

    mapper = sa_inspect(DN)
    mapper.add_property(column_name, table.c[column_name])


def refresh_dynamic_columns(bind: Engine | Session | None = None) -> List[str]:
    """Reload the list of dynamic columns from the database."""

    engine_obj = _get_engine(bind)
    inspector = sa_inspect(engine_obj)
    columns_info = inspector.get_columns("dn")

    dynamic: List[str] = []
    for info in columns_info:
        name = info.get("name")
        if not name or name in _BASE_DN_COLUMN_SET:
            continue
        dynamic.append(name)
        _register_column_on_model(name)

    global _dynamic_columns
    _dynamic_columns = dynamic
    return list(_dynamic_columns)


def ensure_dynamic_columns_loaded(bind: Engine | Session | None = None) -> None:
    """Populate the cached list of dynamic DN columns if it is currently empty."""

    if not _dynamic_columns:
        refresh_dynamic_columns(bind)


def get_dynamic_columns() -> List[str]:
    return list(_dynamic_columns)


def get_sheet_columns() -> List[str]:
    return SHEET_BASE_COLUMNS + list(_dynamic_columns)


def get_mutable_dn_columns(bind: Engine | Session | None = None) -> List[str]:
    ensure_dynamic_columns_loaded(bind)
    allowed = [
        column
        for column in list(_BASE_DN_COLUMN_SET | set(_dynamic_columns))
        if column not in _IMMUTABLE_COLUMNS
    ]
    return allowed


def filter_assignable_dn_fields(
    fields: Mapping[str, object],
    allowed_columns: Collection[str] | None = None,
) -> dict[str, object]:
    """Return a dict that only includes DN columns that can be updated."""

    if allowed_columns is None:
        ensure_dynamic_columns_loaded()
        allowed: Collection[str] | None = get_mutable_dn_columns()
    else:
        allowed = allowed_columns

    if isinstance(allowed, set):
        allowed_set = allowed
    else:
        allowed_set = set(allowed)

    result: dict[str, object] = {}
    for key, value in fields.items():
        if key in allowed_set:
            result[key] = value
    return result


def extend_dn_columns(db: Session, column_names: Iterable[str]) -> List[str]:
    """Ensure the DN table contains the provided columns."""

    ensure_dynamic_columns_loaded(db)
    engine_obj = _get_engine(db)
    inspector = sa_inspect(engine_obj)
    existing_columns = {info["name"] for info in inspector.get_columns("dn")}

    added: List[str] = []

    for raw_name in column_names:
        name = raw_name.strip()
        if not name:
            continue
        if name in existing_columns:
            continue
        if name in _BASE_DN_COLUMN_SET:
            continue
        if not _COLUMN_NAME_PATTERN.fullmatch(name):
            raise ValueError(f"Invalid column name: {name}")

        logger.info("Adding DN column '%s' to database", name)
        db.execute(text(f'ALTER TABLE "dn" ADD COLUMN "{name}" TEXT'))
        added.append(name)
        existing_columns.add(name)

    if added:
        db.commit()
        # Update ORM mapping and cache
        refresh_dynamic_columns(engine_obj)
    return added


__all__ = [
    "SHEET_BASE_COLUMNS",
    "ensure_dynamic_columns_loaded",
    "extend_dn_columns",
    "filter_assignable_dn_fields",
    "get_sheet_columns",
    "get_dynamic_columns",
    "get_mutable_dn_columns",
    "refresh_dynamic_columns",
]
