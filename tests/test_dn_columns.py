from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.dn_columns import ensure_base_dn_columns
from app.db import Base
from app.models import DN


def _create_partial_dn_table(engine):
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS dn"))
        conn.execute(
            text(
                """
                CREATE TABLE dn (
                    id INTEGER PRIMARY KEY,
                    dn_number TEXT
                )
                """
            )
        )


def test_ensure_base_dn_columns_adds_missing_columns(tmp_path):
    db_path = tmp_path / "dn_base.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _create_partial_dn_table(engine)

    added = ensure_base_dn_columns(engine)

    inspector = inspect(engine)
    columns = {info["name"] for info in inspector.get_columns("dn")}
    expected_missing = [
        column.name
        for column in DN.__table__.columns
        if column.name not in {"id", "dn_number"}
    ]

    assert added == expected_missing
    for name in expected_missing:
        assert name in columns

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO dn (dn_number) VALUES ('DN-1')"))
        row = conn.execute(
            text("SELECT dn_number, created_at, status FROM dn WHERE dn_number='DN-1'")
        ).fetchone()
        assert row[0] == "DN-1"
        # created_at should be filled by the DEFAULT expression.
        assert row[1] is not None
        # Newly added nullable columns should default to NULL.
        assert row[2] is None


def test_ensure_base_dn_columns_accepts_session(tmp_path):
    db_path = tmp_path / "dn_session.db"
    engine = create_engine(f"sqlite:///{db_path}")
    _create_partial_dn_table(engine)

    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with SessionLocal() as session:
        added = ensure_base_dn_columns(session)
        # The helper commits internally; ensure the session is clean afterwards.
        assert not session.dirty
        assert not session.new

    inspector = inspect(engine)
    columns = {info["name"] for info in inspector.get_columns("dn")}
    expected_missing = {
        column.name for column in DN.__table__.columns if column.name not in {"id", "dn_number"}
    }

    assert set(added) == expected_missing
    assert expected_missing.issubset(columns)


def test_ensure_base_dn_columns_noop_when_schema_complete(tmp_path):
    db_path = tmp_path / "dn_complete.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)

    added = ensure_base_dn_columns(engine)
    assert added == []
