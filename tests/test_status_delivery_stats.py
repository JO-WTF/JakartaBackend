import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")


from app.crud import (  # noqa: E402
    get_dn_status_delivery_counts,
    get_dn_status_delivery_lsp_counts,
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
):
    db_session.add(
        DN(
            dn_number=dn_number,
            lsp=lsp,
            plan_mos_date=plan_mos_date,
            status=status,
            status_delivery=status_delivery,
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
    db_session.commit()

    status_counts = get_dn_status_delivery_counts(
        db_session, plan_mos_date="01 Jan 25"
    )
    assert dict(status_counts) == {"NO STATUS": 1, "On Site": 1, "POD": 1}

    lsp_counts = get_dn_status_delivery_lsp_counts(
        db_session, plan_mos_date="01 Jan 25"
    )
    assert lsp_counts == [("Alpha", 2, 1), ("NO LSP", 1, 0)]

    alpha_only = get_dn_status_delivery_lsp_counts(
        db_session, lsp=" Alpha", plan_mos_date="01 Jan 25"
    )
    assert alpha_only == [("Alpha", 2, 1)]
