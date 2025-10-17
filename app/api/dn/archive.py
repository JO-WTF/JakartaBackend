from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi import APIRouter

from app.core.google import SPREADSHEET_URL, create_gspread_client
from app.core.sheet import fetch_plan_sheets, parse_date
from app.dn_columns import get_sheet_columns
from app.utils.time import TZ_GMT7
from app.utils.logging import dn_sync_logger
import gspread.utils


# FastAPI router expected by app.api.dn.__init__
router = APIRouter(prefix="/api/dn")


def _col_letter_for_index(idx: int) -> str:
    """Return column letter (A, B, ..., Z, AA, ...) for 1-based index."""
    return gspread.utils.rowcol_to_a1(1, idx)[0:-1]


def ensure_rows(spreadsheet, worksheet, min_rows: int) -> None:
    """确保工作表至少有 min_rows 行；若不足则扩展。"""
    try:
        current = int(getattr(worksheet, "row_count", 0) or 0)
        if current >= min_rows:
            return

        add = min_rows - current
        req = {
            "insertDimension": {
                "range": {
                    "sheetId": worksheet.id,
                    "dimension": "ROWS",
                    "startIndex": current,
                    "endIndex": current + add,
                },
                "inheritFromBefore": False,
            }
        }

        spreadsheet.batch_update({"requests": [req]})

        # ⚠️ gspread 不会自动刷新 worksheet.row_count，这里手动刷新
        worksheet = spreadsheet.worksheet(worksheet.title)
        new_rows = getattr(worksheet, "row_count", 0)
        if new_rows < min_rows:
            # 再次兜底，防止行数仍未更新
            worksheet.add_rows(add)
            dn_sync_logger.debug(f"ensure_rows fallback: added {add} rows manually")

    except Exception as exc:  # pragma: no cover
        dn_sync_logger.exception(f"ensure_rows failed: {exc}")


@router.post("/archive")
def archive_plan_mos():
    """Archive rows in Plan MOS* sheets older than 3 days with status_delivery == POD.

    Steps (per sheet):
    - copy rows 4..end locally
    - clear rows 4..end remotely (do not delete rows)
    - classify rows into keep/archive according to rules
    - write keep rows back to original sheet starting at row 4
    - append archive rows to remote sheet named 'Archived YYYY-MM'
    """

    try:
        gc = create_gspread_client()
        sh = gc.open_by_url(SPREADSHEET_URL)
        plan_sheets = fetch_plan_sheets(sh)

        # refresh in-memory map
        try:
            from app import state

            state.update_gs_map_from_sheets(plan_sheets)
        except Exception:
            dn_sync_logger.debug("failed to refresh sheet map")

        columns = get_sheet_columns()
        col_count = len(columns)
        threshold_date = (datetime.now(TZ_GMT7) - timedelta(days=2)).date()

        # 自动使用分月归档表
        archived_title = f"Archived {datetime.now(TZ_GMT7).strftime('%Y-%m')}"
        try:
            archived_sheet = sh.worksheet(archived_title)
        except Exception:
            try:
                archived_sheet = sh.add_worksheet(title=archived_title, rows=200, cols=col_count)
                dn_sync_logger.info(f"Created new archive sheet: {archived_title}")
            except Exception:
                dn_sync_logger.exception(f"Failed to create '{archived_title}' worksheet")
                archived_sheet = None

        processed: List[Dict[str, Any]] = []

        for ws in plan_sheets:
            try:
                # === 1. 备份当前工作表 ===
                now = datetime.now(TZ_GMT7)
                backup_title = f"Backup {ws.title} {now.strftime('%m-%d %H:%M')}"
                try:
                    sh.batch_update(
                        {
                            "requests": [
                                {
                                    "duplicateSheet": {
                                        "sourceSheetId": ws.id,
                                        "insertSheetIndex": 0,
                                        "newSheetName": backup_title,
                                    }
                                }
                            ]
                        }
                    )
                except Exception as exc:
                    dn_sync_logger.exception(f"Failed to backup sheet {ws.title}: {exc}")

                values = ws.get_all_values()
                if len(values) <= 3:
                    processed.append({"sheet": ws.title, "kept": 0, "archived": 0})
                    dn_sync_logger.info(f"[{ws.title}] 无数据可归档（<=3行）")
                    time.sleep(5)
                    continue

                data_rows = values[3:]
                normalized_rows: List[List[str]] = []
                for row in data_rows:
                    row_cut = row[:col_count]
                    if len(row_cut) < col_count:
                        row_cut = row_cut + [""] * (col_count - len(row_cut))
                    normalized_rows.append(row_cut)

                keep_rows: List[List[str]] = []
                archive_rows: List[List[str]] = []

                try:
                    plan_idx = columns.index("plan_mos_date")
                except ValueError:
                    plan_idx = None
                try:
                    status_idx = columns.index("status_delivery")
                except ValueError:
                    status_idx = None

                for r in normalized_rows:
                    take_archive = False
                    plan_val = r[plan_idx] if plan_idx is not None else ""
                    status_val = r[status_idx] if status_idx is not None else ""
                    parsed = parse_date(plan_val) if plan_val else None
                    plan_date = parsed.date() if isinstance(parsed, datetime) else None

                    if plan_date and plan_date < threshold_date and (status_val or "").strip().upper() == "POD":
                        take_archive = True

                    if take_archive:
                        archive_rows.append(r)
                    else:
                        keep_rows.append(r)

                # === 2. 清空原数据区域 ===
                last_col_letter = _col_letter_for_index(col_count)
                clear_range = f"A4:{last_col_letter}{ws.row_count}"
                try:
                    ws.batch_clear([clear_range])
                except Exception:
                    try:
                        ws.batch_clear([f"A4:{last_col_letter}1000"])
                    except Exception:
                        dn_sync_logger.exception("Failed to clear data range on sheet %s", ws.title)

                # === 3. 写回保留数据 ===
                if keep_rows:
                    needed_rows = 3 + len(keep_rows)
                    ensure_rows(sh, ws, needed_rows)
                    start = 4
                    end = start + len(keep_rows) - 1
                    write_range = f"{ws.title}!A{start}:{last_col_letter}{end}"
                    sh.values_batch_update(
                        {
                            "valueInputOption": "USER_ENTERED",
                            "data": [{"range": write_range, "majorDimension": "ROWS", "values": keep_rows}],
                        }
                    )

                # === 4. 写入归档数据 ===
                if archived_sheet and archive_rows:
                    try:
                        archived_values = archived_sheet.get_all_values()
                        archived_used = len(archived_values)
                        start_row = archived_used + 1 if archived_used >= 1 else 1
                    except Exception:
                        archived_used = 0
                        start_row = 1

                    a_last_col = _col_letter_for_index(col_count)
                    a_start = start_row
                    a_end = a_start + len(archive_rows) - 1

                    # ✅ 双重保险，确保归档表行数足够
                    ensure_rows(sh, archived_sheet, a_end + 1)

                    archive_range = f"{archived_sheet.title}!A{a_start}:{a_last_col}{a_end}"
                    sh.values_batch_update(
                        {
                            "valueInputOption": "USER_ENTERED",
                            "data": [{"range": archive_range, "majorDimension": "ROWS", "values": archive_rows}],
                        }
                    )

                # === 5. 日志与延时 ===
                processed.append({"sheet": ws.title, "kept": len(keep_rows), "archived": len(archive_rows)})
                dn_sync_logger.info(f"[{ws.title}] 处理完成：保留 {len(keep_rows)} 行，归档 {len(archive_rows)} 行。")
                time.sleep(5)

            except Exception as exc:
                dn_sync_logger.exception("Failed processing sheet %s: %s", ws.title, exc)
                processed.append({"sheet": ws.title, "error": str(exc)})
                time.sleep(5)

        # === 汇总日志 ===
        total_archived = sum(p.get("archived", 0) for p in processed if "archived" in p)
        total_kept = sum(p.get("kept", 0) for p in processed if "kept" in p)
        dn_sync_logger.info(f"归档任务完成，共保留 {total_kept} 行，归档 {total_archived} 行。")

        return {"threshold_date": threshold_date.isoformat(), "processed": processed}

    except Exception as exc:
        dn_sync_logger.exception("archive_plan_mos failed: %s", exc)
        return {"error": str(exc)}
