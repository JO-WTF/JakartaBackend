"""提供数据库仓储与工作单元模式的实现。"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from .dn_columns import filter_assignable_dn_fields
from .models import DN, DNRecord, DNSyncLog, DU, DURecord


class BaseRepository:
    """封装 SQLAlchemy Session 的基础仓储类。"""

    def __init__(self, session: Session) -> None:
        """记录共享的数据库会话。"""

        self.session = session


class DURepository(BaseRepository):
    """管理 DU 及其历史记录的仓储。"""

    def ensure(self, du_id: str) -> DU:
        """确保 DU 主表存在指定 ID，不存在则创建。"""

        du = self.session.query(DU).filter(DU.du_id == du_id).one_or_none()
        if not du:
            du = DU(du_id=du_id)
            self.session.add(du)
            self.session.flush()
        return du

    def add_record(
        self,
        *,
        du_id: str,
        status: str,
        remark: str | None,
        photo_url: str | None,
        lng: str | None,
        lat: str | None,
    ) -> DURecord:
        """新增一条 DU 历史记录。"""

        record = DURecord(
            du_id=du_id,
            status=status,
            remark=remark,
            photo_url=photo_url,
            lng=lng,
            lat=lat,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def list_records(self, du_id: str, *, limit: int = 50) -> List[DURecord]:
        """按创建时间倒序查询指定 DU 的历史记录。"""

        return (
            self.session.query(DURecord)
            .filter(DURecord.du_id == du_id)
            .order_by(DURecord.created_at.desc())
            .limit(limit)
            .all()
        )

    def search_records(
        self,
        *,
        du_id: Optional[str] = None,
        status: Optional[str] = None,
        remark_keyword: Optional[str] = None,
        has_photo: Optional[bool] = None,
        date_from=None,
        date_to=None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[int, List[DURecord]]:
        """根据多种条件搜索 DU 历史记录并分页。"""

        query = self.session.query(DURecord)
        conditions = []
        if du_id:
            conditions.append(DURecord.du_id == du_id)
        if status:
            conditions.append(DURecord.status == status)
        if remark_keyword:
            conditions.append(DURecord.remark.ilike(f"%{remark_keyword}%"))
        if has_photo is True:
            conditions.append(DURecord.photo_url.isnot(None))
        elif has_photo is False:
            conditions.append(DURecord.photo_url.is_(None))
        if date_from is not None:
            conditions.append(DURecord.created_at >= date_from)
        if date_to is not None:
            conditions.append(DURecord.created_at <= date_to)
        if conditions:
            query = query.filter(and_(*conditions))

        total = query.count()
        items = (
            query.order_by(DURecord.created_at.desc(), DURecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return total, items

    def get(self, record_id: int) -> Optional[DURecord]:
        """按主键获取单条 DU 历史记录。"""

        return self.session.query(DURecord).get(record_id)

    def update(
        self,
        record_id: int,
        *,
        status: Optional[str] = None,
        remark: Optional[str] = None,
        photo_url: Optional[str] = None,
    ) -> Optional[DURecord]:
        """更新 DU 历史记录的可变字段。"""

        record = self.session.query(DURecord).get(record_id)
        if not record:
            return None
        if status is not None:
            record.status = status
        if remark is not None:
            record.remark = remark
        if photo_url is not None:
            record.photo_url = photo_url
        self.session.add(record)
        self.session.flush()
        return record

    def delete(self, record_id: int) -> bool:
        """删除指定的 DU 历史记录。"""

        record = self.session.query(DURecord).get(record_id)
        if not record:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def list_by_ids(
        self,
        du_ids: Iterable[str],
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[int, List[DURecord]]:
        """按多个 DU ID 查询历史记录并分页。"""

        normalized = [value for value in {value for value in du_ids if value}]
        if not normalized:
            return 0, []
        query = self.session.query(DURecord).filter(DURecord.du_id.in_(normalized))
        total = query.count()
        items = (
            query.order_by(DURecord.created_at.desc(), DURecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return total, items

    def get_existing_ids(self, du_ids: Iterable[str]) -> Set[str]:
        """返回数据库中已存在的 DU ID 集合。"""

        unique_ids = {du_id for du_id in du_ids if du_id}
        if not unique_ids:
            return set()
        rows = (
            self.session.query(DU.du_id)
            .filter(DU.du_id.in_(unique_ids))
            .all()
        )
        return {row[0] for row in rows}


class DNRepository(BaseRepository):
    """管理 DN 主表与历史记录的仓储。"""

    def ensure(self, dn_number: str, **fields: str | None) -> DN:
        """确保 DN 主表存在，并根据传入字段进行更新。"""

        assignable = filter_assignable_dn_fields(fields)
        non_null_assignable = {k: v for k, v in assignable.items() if v is not None}

        dn = self.session.query(DN).filter(DN.dn_number == dn_number).one_or_none()
        if not dn:
            dn = DN(dn_number=dn_number, **non_null_assignable)
            self.session.add(dn)
            self.session.flush()
            return dn

        updated = False
        for key, value in non_null_assignable.items():
            if getattr(dn, key, None) != value:
                setattr(dn, key, value)
                updated = True

        if updated:
            self.session.add(dn)
            self.session.flush()
        return dn

    def delete(self, dn_number: str) -> bool:
        """删除 DN 主表记录及对应的历史数据。"""

        dn = self.session.query(DN).filter(DN.dn_number == dn_number).one_or_none()
        if not dn:
            return False
        self.session.query(DNRecord).filter(DNRecord.dn_number == dn_number).delete(
            synchronize_session=False
        )
        self.session.delete(dn)
        self.session.flush()
        return True

    def add_record(
        self,
        *,
        dn_number: str,
        du_id: Optional[str],
        status: Optional[str],
        remark: Optional[str],
        photo_url: Optional[str],
        lng: Optional[str],
        lat: Optional[str],
    ) -> DNRecord:
        """新增一条 DN 历史记录。"""

        record = DNRecord(
            dn_number=dn_number,
            du_id=du_id,
            status=status,
            remark=remark,
            photo_url=photo_url,
            lng=lng,
            lat=lat,
        )
        self.session.add(record)
        self.session.flush()
        return record

    def list_records(self, dn_number: str, *, limit: int = 50) -> List[DNRecord]:
        """按创建时间倒序获取指定 DN 的历史记录。"""

        return (
            self.session.query(DNRecord)
            .filter(DNRecord.dn_number == dn_number)
            .order_by(DNRecord.created_at.desc())
            .limit(limit)
            .all()
        )

    def list_all(self) -> List[DNRecord]:
        """返回所有 DN 历史记录。"""

        return (
            self.session.query(DNRecord)
            .order_by(DNRecord.created_at.desc(), DNRecord.id.desc())
            .all()
        )

    def list_all_dn(self) -> List[DN]:
        """返回 DN 主表的全部记录。"""

        return self.session.query(DN).order_by(DN.dn_number.asc()).all()

    def search_records(
        self,
        *,
        dn_number: Optional[str] = None,
        du_id: Optional[str] = None,
        status: Optional[str] = None,
        remark_keyword: Optional[str] = None,
        has_photo: Optional[bool] = None,
        date_from=None,
        date_to=None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[int, List[DNRecord]]:
        """根据条件组合搜索 DN 历史记录并分页。"""

        query = self.session.query(DNRecord)
        conditions = []
        if dn_number:
            conditions.append(DNRecord.dn_number == dn_number)
        if du_id:
            conditions.append(DNRecord.du_id == du_id)
        if status:
            conditions.append(DNRecord.status == status)
        if remark_keyword:
            conditions.append(DNRecord.remark.ilike(f"%{remark_keyword}%"))
        if has_photo is True:
            conditions.append(DNRecord.photo_url.isnot(None))
        elif has_photo is False:
            conditions.append(DNRecord.photo_url.is_(None))
        if date_from is not None:
            conditions.append(DNRecord.created_at >= date_from)
        if date_to is not None:
            conditions.append(DNRecord.created_at <= date_to)
        if conditions:
            query = query.filter(and_(*conditions))

        total = query.count()
        items = (
            query.order_by(DNRecord.created_at.desc(), DNRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return total, items

    def get(self, record_id: int) -> Optional[DNRecord]:
        """按主键获取单条 DN 历史记录。"""

        return self.session.query(DNRecord).get(record_id)

    def update(
        self,
        record_id: int,
        *,
        status: Optional[str] = None,
        remark: Optional[str] = None,
        photo_url: Optional[str] = None,
        du_id: Optional[str] = None,
        du_id_set: bool = False,
    ) -> Optional[DNRecord]:
        """更新 DN 历史记录并按需修改关联的 DU。"""

        record = self.session.query(DNRecord).get(record_id)
        if not record:
            return None
        if status is not None:
            record.status = status
        if remark is not None:
            record.remark = remark
        if photo_url is not None:
            record.photo_url = photo_url
        if du_id_set:
            record.du_id = du_id
        elif du_id is not None:
            record.du_id = du_id
        self.session.add(record)
        self.session.flush()
        return record

    def delete_record(self, record_id: int) -> bool:
        """删除指定的 DN 历史记录。"""

        record = self.session.query(DNRecord).get(record_id)
        if not record:
            return False
        self.session.delete(record)
        self.session.flush()
        return True

    def list_records_by_numbers(
        self,
        dn_numbers: Iterable[str],
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[int, List[DNRecord]]:
        """按照 DN 编号集合分页返回历史记录。"""

        numbers = [number for number in {number for number in dn_numbers if number}]
        if not numbers:
            return 0, []
        query = self.session.query(DNRecord).filter(DNRecord.dn_number.in_(numbers))
        total = query.count()
        items = (
            query.order_by(DNRecord.created_at.desc(), DNRecord.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return total, items

    def list_dn_by_numbers(
        self,
        dn_numbers: Iterable[str],
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[int, List[DN]]:
        """根据 DN 编号集合分页返回主表数据，保留输入顺序。"""

        numbers = [number for number in dict.fromkeys(dn_numbers) if number]
        if not numbers:
            return 0, []
        query = self.session.query(DN).filter(DN.dn_number.in_(numbers))
        total = query.count()
        ordering = case(
            *[(number, index) for index, number in enumerate(numbers)],
            value=DN.dn_number,
            else_=len(numbers),
        )
        items = (
            query.order_by(ordering)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return total, items

    def get_existing_numbers(self, dn_numbers: Iterable[str]) -> Set[str]:
        """返回数据库中已存在的 DN 编号集合。"""

        unique_numbers = {dn_number for dn_number in dn_numbers if dn_number}
        if not unique_numbers:
            return set()
        rows = (
            self.session.query(DN.dn_number)
            .filter(DN.dn_number.in_(unique_numbers))
            .all()
        )
        return {row[0] for row in rows}

    def get_latest_records_map(self, dn_numbers: Iterable[str]) -> Dict[str, DNRecord]:
        """构建 DN 编号到最新历史记录的映射。"""

        unique_numbers = [number for number in {number for number in dn_numbers if number}]
        if not unique_numbers:
            return {}
        query = (
            self.session.query(DNRecord)
            .filter(DNRecord.dn_number.in_(unique_numbers))
            .order_by(DNRecord.dn_number.asc(), DNRecord.created_at.desc(), DNRecord.id.desc())
        )
        latest: Dict[str, DNRecord] = {}
        for record in query:
            key = record.dn_number
            if key not in latest:
                latest[key] = record
                if len(latest) == len(unique_numbers):
                    break
        return latest

    def search_dn_list(
        self,
        *,
        plan_mos_dates: Sequence[str] | None = None,
        dn_number: str | None = None,
        du_id: str | None = None,
        status_values: Sequence[str] | None = None,
        status_delivery_values: Sequence[str] | None = None,
        status_not_empty: bool | None = None,
        has_coordinate: bool | None = None,
        lsp_values: Sequence[str] | None = None,
        region_values: Sequence[str] | None = None,
        area: str | None = None,
        status_wh_values: Sequence[str] | None = None,
        subcon_values: Sequence[str] | None = None,
        project_request: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[int, List[DN]]:
        """按照看板筛选条件查询 DN 主表并分页。"""

        query = self.session.query(DN)
        conditions = []
        trimmed_plan_mos_dates = [
            value.strip()
            for value in (plan_mos_dates or [])
            if isinstance(value, str) and value.strip()
        ]
        if trimmed_plan_mos_dates:
            conditions.append(func.trim(DN.plan_mos_date).in_(trimmed_plan_mos_dates))
        if dn_number:
            conditions.append(DN.dn_number == dn_number)
        if du_id:
            conditions.append(DN.du_id == du_id)
        normalized_status_values = [
            value.strip().lower()
            for value in (status_values or [])
            if isinstance(value, str) and value.strip()
        ]
        if normalized_status_values:
            conditions.append(
                func.lower(func.trim(DN.status)).in_(normalized_status_values)
            )
        normalized_status_delivery = [
            value.strip().lower()
            for value in (status_delivery_values or [])
            if isinstance(value, str) and value.strip()
        ]
        if normalized_status_delivery:
            conditions.append(
                func.lower(func.trim(DN.status_delivery)).in_(normalized_status_delivery)
            )
        if status_not_empty is True:
            conditions.append(
                and_(
                    DN.status.isnot(None),
                    func.length(func.trim(DN.status)) > 0,
                )
            )
        elif status_not_empty is False:
            conditions.append(
                or_(
                    DN.status.is_(None),
                    func.length(func.trim(DN.status)) == 0,
                )
            )
        if has_coordinate is True:
            conditions.append(
                and_(
                    DN.lat.isnot(None),
                    func.length(func.trim(DN.lat)) > 0,
                    DN.lng.isnot(None),
                    func.length(func.trim(DN.lng)) > 0,
                )
            )
        elif has_coordinate is False:
            conditions.append(
                or_(
                    DN.lat.is_(None),
                    DN.lng.is_(None),
                    func.length(func.trim(DN.lat)) == 0,
                    func.length(func.trim(DN.lng)) == 0,
                )
            )
        trimmed_lsp_values = [
            value.strip()
            for value in (lsp_values or [])
            if isinstance(value, str) and value.strip()
        ]
        if trimmed_lsp_values:
            conditions.append(func.trim(DN.lsp).in_(trimmed_lsp_values))
        trimmed_region_values = [
            value.strip()
            for value in (region_values or [])
            if isinstance(value, str) and value.strip()
        ]
        if trimmed_region_values:
            conditions.append(func.trim(DN.region).in_(trimmed_region_values))
        if area:
            conditions.append(DN.area == area)
        trimmed_status_wh_values = [
            value.strip()
            for value in (status_wh_values or [])
            if isinstance(value, str) and value.strip()
        ]
        if trimmed_status_wh_values:
            conditions.append(func.trim(DN.status_wh).in_(trimmed_status_wh_values))
        trimmed_subcon_values = [
            value.strip()
            for value in (subcon_values or [])
            if isinstance(value, str) and value.strip()
        ]
        if trimmed_subcon_values:
            conditions.append(func.trim(DN.subcon).in_(trimmed_subcon_values))
        if project_request:
            conditions.append(DN.project_request == project_request)
        if conditions:
            query = query.filter(and_(*conditions))

        total = query.count()
        items = (
            query.order_by(DN.created_at.desc(), DN.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return total, items

    def get_unique_field_values(self) -> Tuple[Dict[str, List[str]], int]:
        """获取供筛选使用的字段去重值以及总记录数。"""

        columns: Dict[str, Any] = {
            "lsp": DN.lsp,
            "region": DN.region,
            "plan_mos_date": DN.plan_mos_date,
            "subcon": DN.subcon,
            "status_wh": DN.status_wh,
            "status_delivery": DN.status_delivery,
        }
        distinct_values: Dict[str, List[str]] = {}
        for key, column in columns.items():
            trimmed = func.trim(column).label("value")
            query = (
                self.session.query(trimmed)
                .filter(column.isnot(None))
                .filter(func.length(trimmed) > 0)
                .distinct()
                .order_by(trimmed.asc())
            )
            distinct_values[key] = [row.value for row in query.all() if row.value]
        total = self.session.query(func.count(DN.id)).scalar() or 0
        return distinct_values, int(total)

    def get_status_delivery_counts(
        self,
        *,
        lsp: Optional[str] = None,
        plan_mos_date: Optional[str] = None,
    ) -> List[tuple[str, int]]:
        """统计各 ``status_delivery`` 的 DN 数量。"""

        status_expr = func.coalesce(
            func.nullif(func.trim(DN.status_delivery), ""), "NO STATUS"
        )
        query = self.session.query(
            status_expr.label("status_delivery"), func.count(DN.id).label("count")
        )
        trimmed_lsp = lsp.strip() if lsp else None
        if trimmed_lsp:
            query = query.filter(func.trim(DN.lsp) == trimmed_lsp)
        trimmed_plan_mos_date = plan_mos_date.strip() if plan_mos_date else None
        if trimmed_plan_mos_date:
            query = query.filter(func.trim(DN.plan_mos_date) == trimmed_plan_mos_date)
        rows = query.group_by(status_expr).order_by(status_expr.asc()).all()
        return [(row.status_delivery, int(row.count)) for row in rows]


class DNSyncLogRepository(BaseRepository):
    """负责同步日志的持久化与查询。"""

    def create(
        self,
        *,
        status: str,
        synced_numbers: Iterable[str] | None = None,
        message: Optional[str] = None,
        error_message: Optional[str] = None,
        error_traceback: Optional[str] = None,
    ) -> DNSyncLog:
        """创建一条新的同步日志。"""

        numbers_list = sorted({str(num) for num in (synced_numbers or []) if str(num)})
        log = DNSyncLog(
            status=status,
            synced_count=len(numbers_list),
            dn_numbers_json=json.dumps(numbers_list) if numbers_list else None,
            message=message,
            error_message=error_message,
            error_traceback=error_traceback,
        )
        self.session.add(log)
        self.session.flush()
        return log

    def get_latest(self) -> Optional[DNSyncLog]:
        """获取最新的同步日志。"""

        return (
            self.session.query(DNSyncLog)
            .order_by(DNSyncLog.created_at.desc(), DNSyncLog.id.desc())
            .first()
        )


class UnitOfWork:
    """工作单元模式：在同一事务中暴露多个仓储。"""

    def __init__(self, session: Session) -> None:
        """保存会话并初始化仓储实例。"""

        self.session = session
        self.du = DURepository(session)
        self.dn = DNRepository(session)
        self.sync_logs = DNSyncLogRepository(session)

    def __enter__(self) -> UnitOfWork:
        """进入上下文管理器时返回自身。"""

        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        """根据执行结果决定提交或回滚事务。"""

        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    def commit(self) -> None:
        """提交当前事务。"""

        self.session.commit()

    def rollback(self) -> None:
        """回滚当前事务。"""

        self.session.rollback()
