from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter
import time

from app.core.google import SPREADSHEET_URL, create_gspread_client
from app.core.sheet import fetch_plan_sheets, parse_date
from app.dn_columns import SHEET_BASE_COLUMNS
from app.utils.time import TZ_GMT7
from app.utils.logging import logger
from app.core.sync import sync_dn_sheet_with_new_session, is_in_maintenance_window
import asyncio


# FastAPI router expected by app.api.dn.__init__
router = APIRouter(prefix="/api/dn")


# =============== 工具函数 ===============


def _safe_sheet_title(title: str) -> str:
    """A1 引用时的工作表名转义。"""
    return "'" + title.replace("'", "''") + "'"


def _parse_plan_date(cell: str) -> Optional[datetime.date]:
    """优先用现有 parse_date，再回退 pandas 解析。"""
    if not cell or not str(cell).strip():
        return None
    v = parse_date(cell)
    if isinstance(v, datetime):
        return v.date()
    try:
        import pandas as _pd

        d = _pd.to_datetime(cell, errors="coerce")
        if str(d) != "NaT":
            return d.date()
    except Exception:
        pass
    return None


def _need_archive(row: List[str], cols: List[str], cutoff: datetime.date) -> bool:
    """判断一行是否需要归档。"""
    try:
        plan_idx = cols.index("plan_mos_date")
        sd_idx = cols.index("status_delivery")
        ss_idx = cols.index("status_site") if "status_site" in cols else None
    except ValueError as e:
        raise RuntimeError(f"缺少必需列: {e}")

    plan_cell = row[plan_idx] if len(row) > plan_idx else ""
    d = _parse_plan_date(plan_cell)
    if not d or d >= cutoff:
        return False

    sd = (row[sd_idx] if len(row) > sd_idx else "").strip().upper()
    ss = (row[ss_idx] if (ss_idx is not None and len(row) > ss_idx) else "").strip().upper()
    return sd == "POD" or ss in ("REPLAN MOS", "CANCEL MOS")


def _normalize_row(row: List[str], width: int) -> List[str]:
    """将行扩展/截断为指定列宽。"""
    if not row:
        return [""] * width
    r = list(row[:width])
    if len(r) < width:
        r += [""] * (width - len(r))
    return r


def _coalesce_row_indices_desc(row_indices_1based: List[int]) -> List[Tuple[int, int]]:
    """
    将 1-based 行号组合成若干连续区间，并按从下到上的顺序返回。
    返回区间为 [start_row, end_row]（均为 1-based，且包含端点）。
    自底向上删除时，直接按该顺序生成 DeleteDimension 请求即可。
    """
    if not row_indices_1based:
        return []
    s = sorted(row_indices_1based)
    blocks: List[Tuple[int, int]] = []
    start = prev = s[0]
    for x in s[1:]:
        if x == prev + 1:
            prev = x
            continue
        blocks.append((start, prev))
        start = prev = x
    blocks.append((start, prev))
    # 自底向上：按 end_row 从大到小排序
    blocks.sort(key=lambda t: t[1], reverse=True)
    return blocks


def _delete_blocks_bottom_up(sh, sheet_id: int, blocks_1based: List[Tuple[int, int]]):
    """
    使用 Sheets API 的 DeleteDimensionRequest 批量删除区间。
    传入的 blocks 为 1-based（包含端点），内部转换为 0-based 半开区间。
    """
    if not blocks_1based:
        return

    requests = []
    for start_1b, end_1b in blocks_1based:
        start0 = start_1b - 1
        end0 = end_1b  # 0-based 的 endIndex 为开区间，恰好等于 1-based 的 end_row
        requests.append(
            {
                "deleteDimension": {
                    "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": start0, "endIndex": end0}
                }
            }
        )

    logger.info("准备删除 %d 个区间 (sheetId=%s): %s", len(requests), sheet_id, blocks_1based)
    # 使用分块执行以避免单次请求过大导致失败；遇到失败时回退到单请求执行
    chunk_size = 200
    for i in range(0, len(requests), chunk_size):
        chunk = requests[i:i + chunk_size]
        try:
            sh.batch_update({"requests": chunk})
            logger.info(
                "删除批次 %d/%d 成功 (sheetId=%s, requests=%d)",
                i // chunk_size + 1,
                (len(requests) - 1) // chunk_size + 1,
                sheet_id,
                len(chunk),
            )
        except Exception as e:
            logger.warning("删除批次 %d 失败 (sheetId=%s): %s", i // chunk_size + 1, sheet_id, e)
            # fallback: 单个请求逐个尝试
            for req in chunk:
                try:
                    sh.batch_update({"requests": [req]})
                except Exception as ee:
                    logger.exception("单请求删除失败 (sheetId=%s): %s", sheet_id, ee)
        # 给 API 一点缓冲时间
        time.sleep(0.1)


def _ensure_archive_sheet(sh, archive_name: str, sheet_columns: List[str]):
    """确保归档表存在且表头正确，返回 (worksheet, header)。"""
    try:
        ws = sh.worksheet(archive_name)
    except Exception:
        logger.info("归档表不存在，创建：%s", archive_name)
        ws = sh.add_worksheet(title=archive_name, rows="1000", cols=str(len(sheet_columns) + 2))

    header = sheet_columns + ["source_sheet", "source_row"]
    try:
        vals = ws.get_all_values()
    except Exception:
        vals = []

    if not vals:
        try:
            ws.append_row(header)
        except Exception:
            logger.exception("写归档表表头失败：%s", archive_name)
    else:
        cur = vals[0]
        if cur != header:
            logger.warning("归档表表头不一致，重写第一行表头")
            try:
                ws.delete_rows(1)
                ws.insert_row(header, index=1)
            except Exception:
                logger.exception("修复归档表表头失败")

    return ws, header


def _append_rows_chunked(ws, rows: List[List[str]], value_input_option: str = "USER_ENTERED", chunk_size: int = 500):
    """分批追加行到归档表，防止 payload 超限并提供回退策略。"""
    if not rows:
        return

    total = len(rows)
    logger.info("准备写入归档表 %s 共 %d 行（分批 %d）", ws.title, total, chunk_size)
    for i in range(0, total, chunk_size):
        chunk = rows[i:i + chunk_size]
        try:
            ws.append_rows(chunk, value_input_option=value_input_option)
            logger.info("写入批次 %d/%d 成功 (%d 行)", i // chunk_size + 1, (total - 1) // chunk_size + 1, len(chunk))
        except Exception as e:
            logger.warning("写入批次 %d 失败 (%d 行): %s", i // chunk_size + 1, len(chunk), e)
            # fallback: 单行追加
            for r in chunk:
                try:
                    ws.append_row(r, value_input_option=value_input_option)
                except Exception as ee:
                    logger.exception("单行追加失败: %s", ee)
        time.sleep(0.2)


# =============== 归档接口（重写版） ===============


@router.get("/archive")
def archive_plan_mos_rows(
    threshold_days: int = 3,
    archive_sheet_name: str = "Archived",
    save_artifacts: bool = False,
    run_sync: bool = True,
) -> Dict[str, Any]:
    """
    新实现：
      1）逐行判断需要归档的行，记录其原始值与原始行号
      2）对同一 sheet 的待删行号合并为若干区间，自底向上用 DeleteDimension 批量删除
      3）删除完成后，将该 sheet 的归档行 append 到归档表
    """
    if threshold_days < 0:
        raise ValueError("threshold_days must be non-negative")

    gc = create_gspread_client()
    sh = gc.open_by_url(SPREADSHEET_URL)
    plan_sheets = fetch_plan_sheets(sh)
    sheet_columns = SHEET_BASE_COLUMNS

    archive_ws, _ = _ensure_archive_sheet(sh, archive_sheet_name, sheet_columns)

    now = datetime.now(TZ_GMT7)
    cutoff = (now - timedelta(days=threshold_days)).date()

    total_processed = 0
    total_archived = 0
    processed_sheets: List[str] = []
    per_sheet_stats: List[Dict[str, Any]] = []

    for ws in plan_sheets:
        t0 = datetime.now()
        try:
            values = ws.get_all_values()
        except Exception:
            logger.exception("读取工作表失败：%s", ws.title)
            continue

        if len(values) <= 3:
            logger.info("工作表 %s 行数 ≤ 3（仅表头），跳过", ws.title)
            continue

        data_rows = values[3:]

        # 逐行判断
        to_archive_values: List[List[str]] = []
        to_delete_rows_1based: List[int] = []  # 要删除的原始行号（1-based）
        for i, raw in enumerate(data_rows, start=4):
            row = _normalize_row(raw, len(sheet_columns))
            if _need_archive(row, sheet_columns, cutoff):
                to_archive_values.append(row + [ws.title, str(i)])
                to_delete_rows_1based.append(i)
            else:
                # row kept; nothing to do
                pass

        processed = len(data_rows)
        archived = len(to_delete_rows_1based)
        total_processed += processed
        total_archived += archived
        processed_sheets.append(ws.title)

        # 2）自底向上删除
        if to_delete_rows_1based:
            blocks = _coalesce_row_indices_desc(to_delete_rows_1based)
            try:
                _delete_blocks_bottom_up(sh, ws.id, blocks)
                logger.info("已自底向上删除 %s：%d 行（%d 个区间）", ws.title, archived, len(blocks))
            except Exception:
                logger.exception("删除失败：%s", ws.title)
                # 不抛出，继续尝试写归档，避免数据丢失

        # 3）删除完成后再写归档
        if to_archive_values:
            try:
                # append_rows 由 Google 负责定位插入末尾，避免我们自己计算 start_row 出错
                archive_ws.append_rows(to_archive_values, value_input_option="USER_ENTERED")
                logger.info("已写入归档表：%s 追加 %d 行", archive_sheet_name, len(to_archive_values))
            except Exception:
                logger.exception("归档表追加失败，尝试 fallback（values_update）")
                try:
                    # fallback：手动算当前归档表已有行数
                    existing = archive_ws.get_all_values()
                    start_row = (len(existing) or 0) + 1
                    # gspread 的 values_update：A1 必须安全转义
                    a1 = f"{_safe_sheet_title(archive_sheet_name)}!A{start_row}"
                    sh.values_update(
                        a1,
                        params={"valueInputOption": "USER_ENTERED"},
                        body={"values": to_archive_values},
                    )
                    logger.info("fallback 写入归档表成功：从 %s 开始的 %d 行", a1, len(to_archive_values))
                except Exception:
                    logger.exception("fallback 写入归档表仍失败：%s", archive_sheet_name)

        dt = (datetime.now() - t0).total_seconds()
        per_sheet_stats.append(
            {
                "sheet": ws.title,
                "processed_rows": processed,
                "archived_rows": archived,
                "duration_seconds": round(dt, 2),
                "deleted_row_blocks": _coalesce_row_indices_desc(to_delete_rows_1based),
            }
        )
        logger.info("Sheet=%s 完成：processed=%d archived=%d (%.2fs)", ws.title, processed, archived, dt)

    summary = {
        "threshold_days": threshold_days,
        "threshold_date": cutoff.isoformat(),
        "processed_sheets": processed_sheets,
        "total_processed_rows": total_processed,
        "total_archived_rows": total_archived,
        "per_sheet": per_sheet_stats,
    }

    result = {"ok": True, "summary": summary}

    # 可选：保存摘要与快照
    if save_artifacts:
        try:
            import time
            import json as _json
            from pathlib import Path

            Path("tmp").mkdir(exist_ok=True)
            ts = time.strftime("%Y%m%dT%H%M%S")
            summary_path = Path("tmp") / f"archive_summary_{ts}.json"
            with open(summary_path, "w", encoding="utf-8") as fh:
                _json.dump(result, fh, ensure_ascii=False, indent=2)
            # 导出归档表快照
            try:
                import pandas as _pd

                snap = archive_ws.get_all_values()
                if snap:
                    df = _pd.DataFrame(snap)
                    excel_path = Path("tmp") / f"archive_snapshot_after_{ts}.xlsx"
                    df.to_excel(excel_path, index=False, header=False)
                    result["artifact_paths"] = {"summary": str(summary_path), "archive_snapshot": str(excel_path)}
                else:
                    result["artifact_paths"] = {"summary": str(summary_path)}
            except Exception:
                result.setdefault("artifact_paths", {})["summary"] = str(summary_path)
        except Exception:
            logger.exception("保存归档摘要/快照失败")

    # 可选：归档完成后立即触发一次同步
    if run_sync:
        if is_in_maintenance_window():
            logger.info("跳过归档后触发的同步：当前处于维护时间窗口 03:58-04:02 GMT+7")
            result["sync"] = {"ok": False, "skipped": True, "reason": "maintenance_window"}
        else:
            try:
                logger.info("触发归档后的 DN sheet -> DB 同步 (manual trigger)")
                sync_result = sync_dn_sheet_with_new_session()
                result["sync"] = {
                    "ok": True,
                    "synced_numbers": sync_result.synced_numbers,
                    "created_count": sync_result.created_count,
                    "updated_count": sync_result.updated_count,
                    "ignored_count": sync_result.ignored_count,
                }
                logger.info(
                    "归档后同步完成: synced=%d created=%d updated=%d ignored=%d",
                    len(sync_result.synced_numbers),
                    sync_result.created_count,
                    sync_result.updated_count,
                    sync_result.ignored_count,
                )
            except Exception as e:
                logger.exception("归档后触发同步失败: %s", e)
                result["sync"] = {"ok": False, "error": str(e)}

    return result


async def scheduled_archive(
    threshold_days: int = 3, archive_sheet_name: str = "Archived", save_artifacts: bool = True, run_sync: bool = True
) -> None:
    """Scheduled wrapper to run archive_plan_mos_rows in a background thread for APScheduler.

    Kept as async to be compatible with AsyncIOScheduler; the actual work runs in a thread.
    """
    try:
        logger.info("Scheduled archive triggered: threshold_days=%s archive_sheet=%s", threshold_days, archive_sheet_name)
        # Run blocking archive in a thread to avoid blocking event loop
        result = await asyncio.to_thread(
            archive_plan_mos_rows, threshold_days, archive_sheet_name, save_artifacts, run_sync
        )
        logger.info(
            "Scheduled archive completed: processed=%s archived=%s",
            result.get("summary", {}).get("total_processed_rows"),
            result.get("summary", {}).get("total_archived_rows"),
        )
    except Exception:
        logger.exception("Scheduled archive failed")
