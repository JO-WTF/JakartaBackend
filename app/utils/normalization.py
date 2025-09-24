"""对外部输入与表格数据进行标准化处理的工具函数集合。"""
from __future__ import annotations

from datetime import datetime
import re
import unicodedata
from typing import Any, Iterable, Iterator, Optional

__all__ = [
    "DU_RE",
    "DN_RE",
    "normalize_du",
    "normalize_dn",
    "normalize_batch_dn_numbers",
    "collect_query_values",
    "parse_date",
    "normalize_sheet_value",
    "strip_optional_string",
]

DU_RE = re.compile(r"^.+$")
DN_RE = re.compile(r"^.+$")

MONTH_MAP = {
    "Sept": "Sep",
}

DATE_FORMATS = [
    "%d %b %y",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d%b",
    "%d %b %y",
    "%d %b %Y",
    "%Y/%m/%d",
]


def normalize_du(value: str) -> str:
    """将 DU 标识去除空白、转换大写并处理全角字符。"""
    if not value:
        return ""

    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.replace("\u200b", "").replace("\ufeff", "")
    normalized = normalized.strip().upper()
    normalized = normalized.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    normalized = normalized.translate(
        str.maketrans(
            "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        )
    )
    return normalized


def normalize_dn(value: str) -> str:
    """按照与 DU 相同的规则标准化 DN 标识。"""
    return normalize_du(value)


def _iter_candidates(values: Iterable[Any]) -> Iterator[str]:
    """遍历可能的字符串集合，展开嵌套序列并过滤空值。"""

    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            yield value
            continue
        try:
            iterator = iter(value)
        except TypeError:
            continue
        for candidate in iterator:
            if isinstance(candidate, str):
                yield candidate


def normalize_batch_dn_numbers(*value_lists: Optional[Iterable[str]]) -> list[str]:
    """批量清洗 DN 编号，拆分逗号、去重并进行格式校验。"""

    raw_numbers: list[str] = []
    for values in value_lists:
        if not values:
            continue
        raw_numbers.extend(values)

    flattened: list[str] = []
    for value in raw_numbers:
        if not value:
            continue
        for part in value.split(","):
            normalized = normalize_dn(part)
            if normalized:
                flattened.append(normalized)

    deduped = [number for number in dict.fromkeys(flattened) if number]
    if not deduped:
        raise ValueError("Missing dn_number")

    invalid = [number for number in deduped if not DN_RE.fullmatch(number)]
    if invalid:
        raise ValueError(f"Invalid DN number(s): {', '.join(invalid)}")

    return deduped


def collect_query_values(*values: Any) -> list[str] | None:
    """归并查询参数中的字符串集合，自动拆分逗号并去重。"""

    normalized: list[str] = []
    seen: set[str] = set()

    for candidate in _iter_candidates(values):
        parts = candidate.split(",") if "," in candidate else [candidate]
        for part in parts:
            trimmed = part.strip()
            if not trimmed or trimmed in seen:
                continue
            seen.add(trimmed)
            normalized.append(trimmed)

    return normalized or None


def strip_optional_string(value: str | None) -> str | None:
    """去除可选字符串两端空白，并在为空时返回 ``None``。"""

    if value is None:
        return None

    stripped = value.strip()
    return stripped or None


def parse_date(date_str: str):
    """尝试按多种常见格式解析日期字符串，失败时返回原值。"""

    for incorrect, correct in MONTH_MAP.items():
        date_str = date_str.replace(incorrect, correct)

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return date_str


def normalize_sheet_value(value: Any) -> Any:
    """将表格中的值转换为便于数据库存储的形式。"""

    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    try:
        import pandas as pd  # type: ignore
    except Exception:  # pragma: no cover - 运行环境默认提供 pandas
        pd = None
    if pd is not None and pd.isna(value):  # type: ignore[attr-defined]
        return None
    return value
