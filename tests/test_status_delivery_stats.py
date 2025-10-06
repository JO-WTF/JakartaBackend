import os

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_DISK_PATH", "./data/uploads")


from app.crud import (  # noqa: E402
    get_dn_status_delivery_counts,
    get_dn_status_delivery_lsp_counts,
    list_status_delivery_lsp_stats,
    search_dn_list,
    upsert_status_delivery_lsp_stats,
)
from app.db import Base  # noqa: E402
from app.models import DN, DNRecord  # noqa: E402
from app.utils.time import TZ_GMT7  # noqa: E402


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _create_dn(
    db_session,
    *,
    dn_number: str,
    lsp: str | None,
    plan_mos_date: str | None,
    status: str | None,
    status_delivery: str | None,
    is_deleted: str = "N",
):
    db_session.add(
        DN(
            dn_number=dn_number,
            lsp=lsp,
            plan_mos_date=plan_mos_date,
            status=status,
            status_delivery=status_delivery,
            is_deleted=is_deleted,
        )
    )


def test_status_delivery_and_lsp_counts(db_session):
    _create_dn(
        db_session,
        dn_number="DN-1",
        lsp="Alpha",
        plan_mos_date="01 Jan 25",
        status="Delivered",
        status_delivery="POD",
    )
    _create_dn(
        db_session,
        dn_number="DN-2",
        lsp="Alpha ",
        plan_mos_date="01 Jan 25",
        status="  ",
        status_delivery="On Site",
    )
    _create_dn(
        db_session,
        dn_number="DN-3",
        lsp="",
        plan_mos_date="01 Jan 25",
        status=None,
        status_delivery=None,
    )
    _create_dn(
        db_session,
        dn_number="DN-4",
        lsp="Beta",
        plan_mos_date="02 Jan 25",
        status="Delivered",
        status_delivery="POD",
    )
    _create_dn(
        db_session,
        dn_number="DN-5",
        lsp="Gamma",
        plan_mos_date="01 Jan 25",
        status="Delivered",
        status_delivery="POD",
        is_deleted="Y",
    )
    db_session.commit()

    status_counts = get_dn_status_delivery_counts(
        db_session, plan_mos_date="01 Jan 25"
    )
    assert dict(status_counts) == {"NO STATUS": 1, "On Site": 1, "POD": 1}

    lsp_counts = get_dn_status_delivery_lsp_counts(
        db_session, plan_mos_date="01 Jan 25"
    )
    # DN-3 has no status_delivery, so it's not counted in total_dn for NO LSP
    assert lsp_counts == [("Alpha", 2, 1), ("NO LSP", 0, 0)]

    alpha_only = get_dn_status_delivery_lsp_counts(
        db_session, lsp=" Alpha", plan_mos_date="01 Jan 25"
    )
    assert alpha_only == [("Alpha", 2, 1)]


def test_status_delivery_stats_response_format(db_session):
    from app.api.dn.stats import get_dn_status_delivery_stats

    _create_dn(
        db_session,
        dn_number="DN-1",
        lsp="Alpha",
        plan_mos_date="01 Jan 25",
        status="Delivered",
        status_delivery="POD",
    )
    _create_dn(
        db_session,
        dn_number="DN-2",
        lsp="Alpha ",
        plan_mos_date="01 Jan 25",
        status="  ",
        status_delivery="On Site",
    )
    _create_dn(
        db_session,
        dn_number="DN-3",
        lsp="",
        plan_mos_date="01 Jan 25",
        status=None,
        status_delivery=None,
    )
    _create_dn(
        db_session,
        dn_number="DN-9",
        lsp="Zeta",
        plan_mos_date="01 Jan 25",
        status="Delivered",
        status_delivery="POD",
        is_deleted="Y",
    )
    db_session.commit()

    response = get_dn_status_delivery_stats(
        lsp=None, plan_mos_date="01 Jan 25", db=db_session
    )
    assert response.model_dump() == {
        "ok": True,
        "data": [
            {"status_delivery": "NO STATUS", "count": 1},
            {"status_delivery": "On Site", "count": 1},
            {"status_delivery": "POD", "count": 1},
        ],
        "total": 3,
        "lsp_summary": [
            {"lsp": "Alpha", "total_dn": 2, "status_not_empty": 1},
            # DN-3 has no status_delivery (not in target statuses), so total_dn is 0
            {"lsp": "NO LSP", "total_dn": 0, "status_not_empty": 0},
        ],
    }


def test_upsert_and_list_lsp_summary_stats(db_session):
    recorded_at = datetime(2025, 1, 1, 10, tzinfo=timezone.utc)

    upsert_status_delivery_lsp_stats(
        db_session,
        [
            {
                "lsp": "Alpha",
                "total_dn": 5,
                "status_not_empty": 3,
                "plan_mos_date": "01 Jan 25",
                "recorded_at": recorded_at,
            },
            {
                "lsp": "Beta",
                "total_dn": 2,
                "status_not_empty": 1,
                "plan_mos_date": "01 Jan 25",
                "recorded_at": recorded_at,
            },
        ],
    )

    # Update existing Alpha snapshot
    upsert_status_delivery_lsp_stats(
        db_session,
        [
            {
                "lsp": "Alpha",
                "total_dn": 6,
                "status_not_empty": 4,
                "plan_mos_date": "01 Jan 25",
                "recorded_at": recorded_at,
            }
        ],
    )

    records = list_status_delivery_lsp_stats(db_session)
    assert [(row.lsp, row.total_dn, row.status_not_empty) for row in records] == [
        ("Alpha", 6, 4),
        ("Beta", 2, 1),
    ]

    alpha_only = list_status_delivery_lsp_stats(db_session, lsp=" Alpha ")
    assert [(row.lsp, row.total_dn, row.status_not_empty) for row in alpha_only] == [
        ("Alpha", 6, 4)
    ]


def test_search_dn_list_excludes_deleted(db_session):
    _create_dn(
        db_session,
        dn_number="ACTIVE-1",
        lsp="Active",
        plan_mos_date="01 Jan 25",
        status="Delivered",
        status_delivery="POD",
    )
    _create_dn(
        db_session,
        dn_number="DELETED-1",
        lsp="Removed",
        plan_mos_date="01 Jan 25",
        status="On Hold",
        status_delivery="On the way",
        is_deleted="Y",
    )
    db_session.commit()

    total, items = search_dn_list(db_session, page=1, page_size=None)

    assert total == 1
    assert [item.dn_number for item in items] == ["ACTIVE-1"]


def test_search_dn_list_filters_by_phone_number(db_session):
    _create_dn(
        db_session,
        dn_number="DN-PHONE-1",
        lsp="Alpha",
        plan_mos_date="01 Jan 25",
        status="Delivered",
        status_delivery="POD",
    )
    _create_dn(
        db_session,
        dn_number="DN-PHONE-2",
        lsp="Beta",
        plan_mos_date="02 Jan 25",
        status="In Transit",
        status_delivery="On Site",
    )
    db_session.commit()

    db_session.add_all(
        [
            DNRecord(
                dn_number="DN-PHONE-1",
                status="Delivered",
                phone_number="081234567890",
            ),
            DNRecord(
                dn_number="DN-PHONE-2",
                status="In Transit",
                phone_number="089876543210",
            ),
        ]
    )
    db_session.commit()

    # phone number stored on associated DNRecord
    total, items = search_dn_list(
        db_session,
        phone_number="089876543210",
        page=1,
        page_size=None,
    )
    assert total == 1
    assert [item.dn_number for item in items] == ["DN-PHONE-2"]

    # ensure trimming is applied when matching driver_contact_number
    dn_one = db_session.query(DN).filter(DN.dn_number == "DN-PHONE-1").one()
    dn_one.driver_contact_number = "081234567890"
    db_session.add(dn_one)
    db_session.commit()

    total_trimmed, items_trimmed = search_dn_list(
        db_session,
        phone_number=" 081234567890 ",
        page=1,
        page_size=None,
    )
    assert total_trimmed == 1
    assert [item.dn_number for item in items_trimmed] == ["DN-PHONE-1"]


def test_get_status_delivery_lsp_summary_records(db_session, monkeypatch):
    from app.api.dn.stats import get_status_delivery_lsp_summary_records

    monkeypatch.setattr(
        "app.api.dn.stats._current_jakarta_hour",
        lambda: datetime(2025, 1, 1, 9, tzinfo=TZ_GMT7),
    )

    recorded_at = datetime(2025, 1, 1, 8, tzinfo=timezone.utc)

    upsert_status_delivery_lsp_stats(
        db_session,
        [
            {
                "lsp": "Alpha",
                "total_dn": 4,
                "status_not_empty": 2,
                "plan_mos_date": "01 Jan 25",
                "recorded_at": recorded_at,
            },
            {
                "lsp": "NO LSP",
                "total_dn": 1,
                "status_not_empty": 0,
                "plan_mos_date": "01 Jan 25",
                "recorded_at": recorded_at,
            },
        ],
    )

    db_session.add_all(
        [
            DN(
                dn_number="DN-A",
                lsp="Alpha",
                plan_mos_date="01 Jan 25",
            ),
            DN(
                dn_number="DN-B",
                lsp="Alpha ",
                plan_mos_date="01 Jan 25",
            ),
        ]
    )
    db_session.add_all(
        [
            DNRecord(
                dn_number="DN-A",
                status="Delivered",
                created_at=datetime(2025, 1, 1, 1, 0, tzinfo=timezone.utc),
            ),
            DNRecord(
                dn_number="DN-B",
                status="Delivered",
                created_at=datetime(2025, 1, 1, 2, 30, tzinfo=timezone.utc),
            ),
        ]
    )
    db_session.commit()

    response = get_status_delivery_lsp_summary_records(
        lsp="Alpha", limit=10, db=db_session
    )
    assert len(response.data.by_plan_mos_date) == 1
    record = response.data.by_plan_mos_date[0]
    assert record.lsp == "Alpha"
    assert record.total_dn == 4
    assert record.status_not_empty == 2
    assert record.plan_mos_date == "01 Jan 25"
    assert record.recorded_at.replace(tzinfo=timezone.utc) == recorded_at

    updates = response.data.by_update_date
    updates_map = {row.recorded_at: row.updated_dn for row in updates}
    for hour in range(0, 8):
        key = f"2025-01-01 {hour:02d}:00:00"
        assert updates_map[key] == 0

    assert updates_map["2025-01-01 08:00:00"] == 1
    assert updates_map["2025-01-01 09:00:00"] == 2


def test_capture_status_delivery_lsp_summary(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", future=True)
    TestingSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    Base.metadata.create_all(engine)

    session = TestingSessionLocal()
    try:
        session.add_all(
            [
                DN(
                    dn_number="DN-1",
                    lsp="Alpha",
                    plan_mos_date="01 Jan 25",
                    status="Delivered",
                    status_delivery="POD",
                ),
                DN(
                    dn_number="DN-2",
                    lsp="Alpha",
                    plan_mos_date="01 Jan 25",
                    status=None,
                    status_delivery="On Site",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    import app.core.status_delivery_summary as summary_module

    monkeypatch.setattr(summary_module, "SessionLocal", TestingSessionLocal)

    records = summary_module.capture_status_delivery_lsp_summary(
        plan_mos_date="01 Jan 25"
    )

    assert len(records) == 1
    record = records[0]
    assert record.lsp == "Alpha"
    assert record.total_dn == 2
    assert record.status_not_empty == 1


def test_get_status_delivery_lsp_summary_records_normalizes_lsp_labels(db_session, monkeypatch):
    from app.api.dn.stats import get_status_delivery_lsp_summary_records

    monkeypatch.setattr(
        "app.api.dn.stats._current_jakarta_hour",
        lambda: datetime(2025, 1, 3, 7, tzinfo=TZ_GMT7),
    )
    snapshot_time = datetime(2025, 1, 2, 8, tzinfo=timezone.utc)

    upsert_status_delivery_lsp_stats(
        db_session,
        [
            {
                "lsp": "NO LSP",
                "total_dn": 2,
                "status_not_empty": 1,
                "plan_mos_date": "02 Jan 25",
                "recorded_at": snapshot_time,
            }
        ],
    )

    db_session.add_all(
        [
            DN(dn_number="DN-NA", lsp="#N/A", plan_mos_date="02 Jan 25"),
            DN(dn_number="DN-NO", lsp=" NO LSP ", plan_mos_date="02 Jan 25"),
            DN(dn_number="DN-EMPTY", lsp=None, plan_mos_date=None),
            DN(dn_number="DN-SUB", lsp="SUBCON", plan_mos_date="02 Jan 25"),
        ]
    )
    db_session.add_all(
        [
            DNRecord(
                dn_number="DN-NA",
                status="Delivered",
                created_at=datetime(2025, 1, 2, 0, 15, tzinfo=timezone.utc),
            ),
            DNRecord(
                dn_number="DN-NO",
                status="Delivered",
                created_at=datetime(2025, 1, 2, 1, 15, tzinfo=timezone.utc),
            ),
            DNRecord(
                dn_number="DN-SUB",
                status="On Site",
                created_at=datetime(2025, 1, 2, 2, 15, tzinfo=timezone.utc),
            ),
            DNRecord(
                dn_number="DN-EMPTY",
                status="POD",
                created_at=datetime(2025, 1, 3, 0, 15, tzinfo=timezone.utc),
            ),
        ]
    )
    db_session.commit()

    response = get_status_delivery_lsp_summary_records(lsp=None, limit=1000, db=db_session)

    updates = response.data.by_update_date
    lookup = {
        (row.lsp, row.recorded_at): row.updated_dn
        for row in updates
    }

    assert lookup[("NO_LSP", "2025-01-02 07:00:00")] == 1
    assert lookup[("NO_LSP", "2025-01-02 08:00:00")] == 2
    assert lookup[("Subcon", "2025-01-02 09:00:00")] == 1
    assert lookup[("NO_LSP_NO_PLAN_MOS_DATE", "2025-01-03 07:00:00")] == 1


def test_get_status_delivery_lsp_summary_records_fills_missing_hours(db_session, monkeypatch):
    from app.api.dn.stats import get_status_delivery_lsp_summary_records

    monkeypatch.setattr(
        "app.api.dn.stats._current_jakarta_hour",
        lambda: datetime(2025, 1, 5, 10, tzinfo=TZ_GMT7),
    )
    db_session.add_all(
        [
            DN(dn_number="DN-GAP-1", lsp="Alpha", plan_mos_date="05 Jan 25"),
            DN(dn_number="DN-GAP-2", lsp="Alpha", plan_mos_date="05 Jan 25"),
        ]
    )
    db_session.add_all(
        [
            DNRecord(
                dn_number="DN-GAP-1",
                status="POD",
                created_at=datetime(2025, 1, 5, 0, 15, tzinfo=timezone.utc),
            ),
            DNRecord(
                dn_number="DN-GAP-2",
                status="On Site",
                created_at=datetime(2025, 1, 5, 3, 45, tzinfo=timezone.utc),
            ),
        ]
    )
    db_session.commit()

    response = get_status_delivery_lsp_summary_records(lsp="Alpha", limit=100, db=db_session)

    updates_map = {row.recorded_at: row.updated_dn for row in response.data.by_update_date}

    for hour in range(0, 7):
        key = f"2025-01-05 {hour:02d}:00:00"
        assert updates_map[key] == 0

    assert updates_map["2025-01-05 07:00:00"] == 1
    assert updates_map["2025-01-05 08:00:00"] == 1
    assert updates_map["2025-01-05 09:00:00"] == 1
    assert updates_map["2025-01-05 10:00:00"] == 2


def test_get_status_delivery_lsp_summary_records_returns_zero_for_quiet_day(db_session, monkeypatch):
    from app.api.dn.stats import get_status_delivery_lsp_summary_records

    monkeypatch.setattr(
        "app.api.dn.stats._current_jakarta_hour",
        lambda: datetime(2025, 1, 6, 5, tzinfo=TZ_GMT7),
    )

    db_session.add(
        DN(
            dn_number="DN-Q-1",
            lsp="Alpha",
            plan_mos_date="05 Jan 25",
        )
    )
    db_session.add(
        DNRecord(
            dn_number="DN-Q-1",
            status="POD",
            created_at=datetime(2025, 1, 5, 0, 15, tzinfo=timezone.utc),
        )
    )
    db_session.commit()

    response = get_status_delivery_lsp_summary_records(lsp="Alpha", limit=1000, db=db_session)

    updates = response.data.by_update_date
    assert any(row.recorded_at == "2025-01-06 00:00:00" and row.updated_dn == 0 for row in updates)
    assert updates[-1].recorded_at == "2025-01-06 05:00:00"
    assert updates[-1].updated_dn == 0
