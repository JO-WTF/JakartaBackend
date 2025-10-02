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
from app.models import DN  # noqa: E402


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


def test_get_status_delivery_lsp_summary_records(db_session):
    from app.api.dn.stats import get_status_delivery_lsp_summary_records

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

    response = get_status_delivery_lsp_summary_records(
        lsp="Alpha", limit=10, db=db_session
    )
    assert len(response.data) == 1
    record = response.data[0]
    assert record.lsp == "Alpha"
    assert record.total_dn == 4
    assert record.status_not_empty == 2
    assert record.plan_mos_date == "01 Jan 25"
    assert record.recorded_at.replace(tzinfo=timezone.utc) == recorded_at


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
