from __future__ import annotations

import logging
import re
from typing import Iterable, List, Mapping

from sqlalchemy import (
    Column,
    Text as SAText,
    inspect as sa_inspect,
    text,
    Integer,
    String,
    DateTime,
    MetaData,
    Table,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from .db import engine
from .models import DN

logger = logging.getLogger(__name__)

# Base columns defined on the SQLAlchemy model when the application starts.
_BASE_DN_COLUMNS = tuple(column.name for column in DN.__table__.columns)
_BASE_DN_COLUMN_SET = set(_BASE_DN_COLUMNS)
# Columns that should never be updated through sheet synchronization.
_IMMUTABLE_COLUMNS = {"id", "dn_number", "created_at"}

_TIME_KEYWORDS = ("time", "date")
_EXPLICIT_TEXT_COLUMNS = {"remark", "photo_url", "issue_remark"}

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
    if not _dynamic_columns:
        refresh_dynamic_columns(bind)


def get_dynamic_columns() -> List[str]:
    return list(_dynamic_columns)


def get_sheet_columns() -> List[str]:
    return SHEET_BASE_COLUMNS + list(_dynamic_columns)


def get_mutable_dn_columns() -> List[str]:
    ensure_dynamic_columns_loaded()
    allowed = [
        column
        for column in list(_BASE_DN_COLUMN_SET | set(_dynamic_columns))
        if column not in _IMMUTABLE_COLUMNS
    ]
    return allowed


def filter_assignable_dn_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Return a dict that only includes DN columns that can be updated."""

    ensure_dynamic_columns_loaded()
    allowed = set(get_mutable_dn_columns())
    result: dict[str, object] = {}
    for key, value in fields.items():
        if key in allowed:
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


def _resolve_column_type(column_name: str):
    lowered = column_name.lower()
    if column_name in _EXPLICIT_TEXT_COLUMNS:
        return SAText
    if any(keyword in lowered for keyword in _TIME_KEYWORDS):
        return SAText
    return String(256)


def reset_dn_table(bind: Engine | Session | None = None) -> List[str]:
    """Drop and recreate the DN table based on the sheet definition."""

    engine_obj = _get_engine(bind)
    table_name = DN.__tablename__

    metadata = MetaData()
    with engine_obj.begin() as connection:
        connection.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))

    created_columns: List[Column] = [
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("dn_number", String(256), nullable=False, unique=True, index=True),
    ]

    seen = {"id", "dn_number"}
    # Preserve all known base columns along with the sheet definition.
    ordered_columns = list(dict.fromkeys(SHEET_BASE_COLUMNS + list(_BASE_DN_COLUMNS)))

    for name in ordered_columns:
        if name in seen or name == "created_at":
            continue
        column_type = _resolve_column_type(name)
        created_columns.append(Column(name, column_type, nullable=True))
        seen.add(name)

    created_columns.append(
        Column(
            "created_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        )
    )

    dn_table = Table(table_name, metadata, *created_columns)
    metadata.create_all(engine_obj, tables=[dn_table])

    # Reset cached dynamic column state as the table has been recreated.
    refresh_dynamic_columns(engine_obj)

    return [col.name for col in dn_table.columns]


__all__ = [
    "SHEET_BASE_COLUMNS",
    "ensure_dynamic_columns_loaded",
    "extend_dn_columns",
    "reset_dn_table",
    "filter_assignable_dn_fields",
    "get_sheet_columns",
    "get_dynamic_columns",
    "get_mutable_dn_columns",
    "refresh_dynamic_columns",
]
