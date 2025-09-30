import os
from datetime import datetime, time, timezone, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_DISK_PATH", "./data/uploads")


from app.db import Base  # noqa: E402
from app.crud import (  # noqa: E402
    upsert_vehicle_signin,
    get_vehicle_by_plate,
    mark_vehicle_departed,
    list_vehicles,
)
from app.time_utils import TZ_GMT7  # noqa: E402


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_vehicle_signin_and_fetch(db_session):
    arrive_time = datetime(2025, 3, 23, 8, 30, tzinfo=TZ_GMT7)

    created = upsert_vehicle_signin(
        db_session,
        vehicle_plate="b 1234 cd",
        lsp="Test LSP",
        vehicle_type="Truck",
        driver_name="Alice",
        contact_number="0812345678",
        arrive_time=arrive_time,
    )

    assert created.vehicle_plate == "B1234CD"
    assert created.status == "arrived"
    assert created.arrive_time is not None

    fetched = get_vehicle_by_plate(db_session, "b1234cd")
    assert fetched is not None
    assert fetched.driver_name == "Alice"
    assert fetched.arrive_time is not None
    stored_arrive = fetched.arrive_time
    if stored_arrive.tzinfo is None:
        stored_arrive = stored_arrive.replace(tzinfo=timezone.utc)
    assert stored_arrive == arrive_time.astimezone(timezone.utc)


def test_vehicle_depart_and_listing(db_session):
    first_arrive = datetime(2025, 3, 23, 7, 0, tzinfo=TZ_GMT7)
    second_arrive = datetime(2025, 3, 24, 9, 15, tzinfo=TZ_GMT7)

    upsert_vehicle_signin(
        db_session,
        vehicle_plate="B9999ZZ",
        lsp="Main LSP",
        arrive_time=first_arrive,
    )

    upsert_vehicle_signin(
        db_session,
        vehicle_plate="B8888YY",
        lsp="Main LSP",
        arrive_time=second_arrive,
    )

    depart_time = first_arrive + timedelta(hours=10)
    departed = mark_vehicle_departed(
        db_session,
        vehicle_plate="b9999zz",
        depart_time=depart_time,
    )

    assert departed is not None
    assert departed.status == "departed"
    assert departed.depart_time is not None

    # Filter departed vehicles on the same day using GMT+7 boundaries
    day = datetime(2025, 3, 23)
    start_local = datetime.combine(day.date(), time(0, 0, tzinfo=TZ_GMT7))
    end_local = datetime.combine(day.date(), time(23, 59, 59, 999999, tzinfo=TZ_GMT7))

    departed_list = list_vehicles(
        db_session,
        status="departed",
        filter_by="depart_time",
        date_from=start_local.astimezone(timezone.utc),
        date_to=end_local.astimezone(timezone.utc),
    )

    assert [vehicle.vehicle_plate for vehicle in departed_list] == ["B9999ZZ"]

    arrived_list = list_vehicles(
        db_session,
        status="arrived",
        filter_by="arrive_time",
        date_from=start_local.astimezone(timezone.utc),
        date_to=end_local.astimezone(timezone.utc),
    )

    assert [vehicle.vehicle_plate for vehicle in arrived_list] == []

