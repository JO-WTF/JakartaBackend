"""封装 DN 业务流程的服务层。"""
from __future__ import annotations

from typing import Iterable, List, Sequence

from ..constants import VALID_STATUSES
from ..crud import UnitOfWork
from ..utils.normalization import DU_RE, DN_RE, normalize_dn, normalize_du


class DNService:
    """负责 DN 的创建、更新、查询与批处理等业务逻辑。"""

    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    def create_update(
        self,
        *,
        dn_number: str,
        status: str,
        remark: str | None,
        du_id: str | None,
        photo_url: str | None,
        lng: str | None,
        lat: str | None,
    ):
        """校验并创建 DN 记录，同时更新最新快照。"""

        normalized_dn = normalize_dn(dn_number)
        if not DN_RE.fullmatch(normalized_dn):
            raise ValueError("Invalid DN number")
        if status not in VALID_STATUSES:
            raise ValueError("Invalid status")

        normalized_du = None
        if du_id:
            normalized_du = normalize_du(du_id)
            if not DU_RE.fullmatch(normalized_du):
                raise ValueError("Invalid DU ID")
            self.uow.du.ensure(normalized_du)

        self.uow.dn.ensure(
            normalized_dn,
            du_id=normalized_du,
            status=status,
            remark=remark,
            photo_url=photo_url,
            lng=lng,
            lat=lat,
        )
        record = self.uow.dn.add_record(
            dn_number=normalized_dn,
            du_id=normalized_du,
            status=status,
            remark=remark,
            photo_url=photo_url,
            lng=lng,
            lat=lat,
        )
        self.uow.dn.ensure(
            record.dn_number,
            du_id=record.du_id,
            status=record.status,
            remark=record.remark,
            photo_url=record.photo_url,
            lng=record.lng,
            lat=record.lat,
        )
        return record

    def batch_create(self, dn_numbers: Iterable[str]) -> dict:
        """批量写入 DN 编号，并返回逐项的校验结果。"""

        seen: set[str] = set()
        normalized_numbers: List[str] = []
        failure_details: dict[str, str] = {}

        for raw in dn_numbers:
            normalized = normalize_dn(raw)
            if not normalized or not DN_RE.fullmatch(normalized):
                key = raw if isinstance(raw, str) and raw else "<empty>"
                failure_details[str(key)] = "无效的 DN number"
                continue
            if normalized in seen:
                failure_details[normalized] = "请求中重复"
                continue
            seen.add(normalized)
            normalized_numbers.append(normalized)

        existing = self.uow.dn.get_existing_numbers(normalized_numbers)
        success_numbers: List[str] = []

        for number in normalized_numbers:
            if number in existing:
                failure_details[number] = "DN number 已存在"
                continue
            self.uow.dn.ensure(number, status="NO STATUS")
            self.uow.dn.add_record(
                dn_number=number,
                du_id=None,
                status="NO STATUS",
                remark=None,
                photo_url=None,
                lng=None,
                lat=None,
            )
            success_numbers.append(number)

        status = "ok" if success_numbers else "fail"
        response = {
            "status": status,
            "success_count": len(success_numbers),
            "failure_count": len(failure_details),
            "success_dn_numbers": success_numbers,
            "failure_details": failure_details,
        }
        if not normalized_numbers and not success_numbers and not failure_details:
            response.update(
                {
                    "status": "fail",
                    "errmessage": "DN number 列表为空",
                    "success_count": 0,
                    "failure_count": 0,
                    "success_dn_numbers": [],
                    "failure_details": {},
                }
            )
        return response

    def search_records(self, **filters):
        """将查询条件转发给仓储层，保持服务接口稳定。"""

        return self.uow.dn.search_records(**filters)

    def list_records_by_numbers(self, dn_numbers: Iterable[str], *, page: int, page_size: int):
        """根据给定 DN 编号返回分页后的历史记录。"""

        normalized_numbers = self._normalize_dn_number_sequence(dn_numbers)
        return self.uow.dn.list_records_by_numbers(
            normalized_numbers, page=page, page_size=page_size
        )

    def list_dn_by_numbers(self, dn_numbers: Iterable[str], *, page: int, page_size: int):
        """根据 DN 编号集合分页返回实体列表，并尽量保持原始顺序。"""

        normalized_numbers = self._normalize_dn_number_sequence(dn_numbers)
        return self.uow.dn.list_dn_by_numbers(
            normalized_numbers, page=page, page_size=page_size
        )

    def _normalize_dn_number_sequence(self, dn_numbers: Iterable[str]) -> List[str]:
        """验证用户提供的 DN 序列并输出标准化结果。"""

        normalized_numbers: List[str] = []
        invalid: List[str] = []

        for value in dn_numbers:
            normalized = normalize_dn(value)
            if not normalized or not DN_RE.fullmatch(normalized):
                invalid.append(str(value))
            else:
                normalized_numbers.append(normalized)

        if invalid:
            raise ValueError(f"Invalid DN number(s): {', '.join(invalid)}")
        if not normalized_numbers:
            raise ValueError("Missing dn_number")

        return normalized_numbers

    def update_record(
        self,
        record_id: int,
        *,
        status: str | None,
        remark: str | None,
        photo_url: str | None,
        du_id: str | None,
        du_id_provided: bool,
    ):
        """更新 DN 历史记录，同时遵循原有的 DU 关联规则。"""

        normalized_status = status
        if normalized_status is not None and normalized_status not in VALID_STATUSES:
            raise ValueError("Invalid status")

        normalized_du = None
        if du_id:
            normalized_du = normalize_du(du_id)
            if not DU_RE.fullmatch(normalized_du):
                raise ValueError("Invalid DU ID")
            self.uow.du.ensure(normalized_du)
        elif du_id_provided:
            normalized_du = None

        record = self.uow.dn.update(
            record_id,
            status=normalized_status,
            remark=remark,
            photo_url=photo_url,
            du_id=normalized_du,
            du_id_set=du_id_provided,
        )
        if not record:
            return None
        self.uow.dn.ensure(
            record.dn_number,
            du_id=record.du_id,
            status=record.status,
            remark=record.remark,
            photo_url=record.photo_url,
            lng=record.lng,
            lat=record.lat,
        )
        return record

    def delete_record(self, record_id: int) -> bool:
        """按主键删除 DN 历史记录。"""

        return self.uow.dn.delete_record(record_id)

    def delete_dn(self, dn_number: str) -> bool:
        """删除 DN 主表记录及其所有历史。"""

        normalized = normalize_dn(dn_number)
        if not DN_RE.fullmatch(normalized):
            raise ValueError("Invalid DN number")
        return self.uow.dn.delete(normalized)

    def list_records(self, dn_number: str):
        """查询指定 DN 的全部历史记录。"""

        normalized = normalize_dn(dn_number)
        if not DN_RE.fullmatch(normalized):
            raise ValueError("Invalid DN number")
        return self.uow.dn.list_records(normalized)

    def list_all_records(self):
        """返回所有 DN 历史记录，按时间倒序排列。"""

        return self.uow.dn.list_all()

    def list_dn_entities(self):
        """返回全部 DN 实体，不进行分页。"""

        return self.uow.dn.list_all_dn()

    def get_latest_records_map(self, dn_numbers: Sequence[str]):
        """返回 DN 编号与其最新历史记录的映射。"""

        return self.uow.dn.get_latest_records_map(dn_numbers)

    def search_dn_list(self, **filters):
        """按看板筛选条件查询 DN 主表。"""

        return self.uow.dn.search_dn_list(**filters)

    def get_unique_field_values(self):
        """收集用于筛选的关键 DN 字段的去重值。"""

        return self.uow.dn.get_unique_field_values()

    def get_status_delivery_counts(self, **filters):
        """根据 ``status_delivery`` 统计 DN 数量。"""

        return self.uow.dn.get_status_delivery_counts(**filters)
