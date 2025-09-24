from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy.orm import Session

from app.models import DNSyncLog
from app.routers.sync import (
    download_dn_sync_log,
    get_dn_stats,
    get_latest_dn_sync_log_entry,
    trigger_dn_sync,
)


def test_trigger_dn_sync_returns_synced_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.routers.sync.perform_sync_with_logging", lambda: ["DN-1", "DN-2"]
    )

    response = asyncio.run(trigger_dn_sync())
    assert response.ok is True
    assert response.synced_count == 2
    assert response.dn_numbers == ["DN-1", "DN-2"]


def test_get_latest_dn_sync_log_returns_entry(
    db_session: Session,
) -> None:
    log = DNSyncLog(
        status="ok",
        synced_count=2,
        dn_numbers_json=json.dumps(["DN-10", "DN-11"]),
        message="completed",
    )
    db_session.add(log)
    db_session.commit()

    response = get_latest_dn_sync_log_entry(db=db_session)
    assert response.data is not None
    assert response.data.synced_count == 2
    assert response.data.dn_numbers == ["DN-10", "DN-11"]
    assert response.data.message == "completed"


def test_download_sync_log_returns_file(monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = Path(os.environ["DN_SYNC_LOG_PATH"])
    log_path.write_text("sync completed", encoding="utf-8")

    response = download_dn_sync_log()
    assert str(response.path) == str(log_path)

    # FileResponse uses a background task to stream content; read the file to verify
    assert log_path.read_text(encoding="utf-8") == "sync completed"


def test_get_dn_stats_aggregates_sheet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DummyClient:
        def open_by_url(self, url: str) -> object:  # pragma: no cover - helper
            return object()

    df = pd.DataFrame(
        [
            {
                "plan_mos_date": "01 Jan 24",
                "region": "Region-1",
                "status_delivery": "on the way",
                "dn_number": "DN-20",
            },
            {
                "plan_mos_date": "01 Jan 24",
                "region": "Region-1",
                "status_delivery": "pod",
                "dn_number": "DN-21",
            },
            {
                "plan_mos_date": "01 Jan 24",
                "region": "Region-2",
                "status_delivery": "",
                "dn_number": "DN-22",
            },
        ]
    )

    monkeypatch.setattr("app.routers.sync.get_gspread_client", lambda: DummyClient())
    monkeypatch.setattr("app.routers.sync.get_spreadsheet_url", lambda: "https://example.com/test-sheet")
    monkeypatch.setattr("app.routers.sync.process_all_sheets", lambda sheet: df.copy())

    response = get_dn_stats(date="01-Jan-24")
    assert response.ok is True
    regions = {row.group for row in response.data}
    assert regions == {"Region-1", "Region-2"}
    first_row = next(row for row in response.data if row.group == "Region-1")
    assert first_row.date == "01-Jan-24"
    assert sum(first_row.values) >= 2
