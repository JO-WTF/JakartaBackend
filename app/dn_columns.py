"""维护 DN 表可扩展列的辅助函数。"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Mapping

from sqlalchemy import Column, Text as SAText, inspect as sa_inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .db import engine
from .models import DN

logger = logging.getLogger(__name__)

# 应用启动时 SQLAlchemy 模型自带的基础字段。
_BASE_DN_COLUMNS = tuple(column.name for column in DN.__table__.columns)
_BASE_DN_COLUMN_SET = set(_BASE_DN_COLUMNS)
# 不允许通过表格同步更新的字段。
_IMMUTABLE_COLUMNS = {"id", "dn_number", "created_at"}

# 与 Google Sheet 结构一致的基础列。
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

# 记录动态新增的 DN 列（按表结构顺序）。
_dynamic_columns: List[str] = []

_COLUMN_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _get_engine(bind: Engine | Session | None = None) -> Engine:
    """根据绑定对象解析出 SQLAlchemy 引擎。"""

    if isinstance(bind, Session):
        if bind.bind is None:
            raise RuntimeError("Session is not bound to an engine")
        return bind.bind
    if isinstance(bind, Engine):
        return bind
    return engine


def _register_column_on_model(column_name: str) -> None:
    """在 SQLAlchemy 模型上注册动态列以供 ORM 使用。"""

    table = DN.__table__
    if column_name in table.c:
        return

    logger.debug("Registering dynamic DN column on model: %s", column_name)
    column = Column(column_name, SAText, nullable=True)
    table.append_column(column)

    mapper = sa_inspect(DN)
    mapper.add_property(column_name, table.c[column_name])


def refresh_dynamic_columns(bind: Engine | Session | None = None) -> List[str]:
    """从数据库重新加载动态列列表。"""

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
    """在缓存为空时重新加载动态列。"""

    if not _dynamic_columns:
        refresh_dynamic_columns(bind)


def get_dynamic_columns() -> List[str]:
    """返回当前已知的动态列列表。"""

    return list(_dynamic_columns)


def get_sheet_columns() -> List[str]:
    """返回同步时使用的所有列名。"""

    return SHEET_BASE_COLUMNS + list(_dynamic_columns)


def get_mutable_dn_columns() -> List[str]:
    """获取允许被更新的 DN 列集合。"""

    ensure_dynamic_columns_loaded()
    allowed = [
        column
        for column in list(_BASE_DN_COLUMN_SET | set(_dynamic_columns))
        if column not in _IMMUTABLE_COLUMNS
    ]
    return allowed


def filter_assignable_dn_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """返回仅包含可写 DN 列的字段字典。"""

    ensure_dynamic_columns_loaded()
    allowed = set(get_mutable_dn_columns())
    result: dict[str, object] = {}
    for key, value in fields.items():
        if key in allowed:
            result[key] = value
    return result


def extend_dn_columns(db: Session, column_names: Iterable[str]) -> List[str]:
    """确保 DN 表新增指定列。"""

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
        # 更新 ORM 映射与缓存
        refresh_dynamic_columns(engine_obj)
    return added


def extend_dn_table_columns(db: Session, column_names: Iterable[str]) -> List[str]:
    """向后兼容的别名，委托给 :func:`extend_dn_columns`。"""

    return extend_dn_columns(db, column_names)


__all__ = [
    "SHEET_BASE_COLUMNS",
    "ensure_dynamic_columns_loaded",
    "extend_dn_columns",
    "extend_dn_table_columns",
    "filter_assignable_dn_fields",
    "get_sheet_columns",
    "get_dynamic_columns",
    "get_mutable_dn_columns",
    "refresh_dynamic_columns",
]
