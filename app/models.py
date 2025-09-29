import json
from sqlalchemy import Column, String, Integer, DateTime, Text, UniqueConstraint
from sqlalchemy.sql import func
from .db import Base


class Vehicle(Base):
    __tablename__ = "vehicle"

    id = Column(Integer, primary_key=True, index=True)
    vehicle_plate = Column(String(32), unique=True, index=True, nullable=False)
    lsp = Column(String(128), nullable=False)
    vehicle_type = Column(String(64), nullable=True)
    driver_name = Column(String(128), nullable=True)
    contact_number = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False, default="arrived")
    arrive_time = Column(DateTime(timezone=True), nullable=True)
    depart_time = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class DN(Base):
    __tablename__ = "dn"
    id = Column(Integer, primary_key=True, index=True)
    dn_number = Column(Text, unique=True, index=True, nullable=False)
    du_id = Column(Text, nullable=True)
    status = Column(Text, nullable=True)
    remark = Column(Text, nullable=True)
    photo_url = Column(Text, nullable=True)
    lng = Column(Text, nullable=True)
    lat = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status_wh = Column(Text, nullable=True)
    lsp = Column(Text, nullable=True)
    area = Column(Text, nullable=True)
    mos_given_time = Column(Text, nullable=True)
    expected_arrival_time_from_project = Column(Text, nullable=True)
    project_request = Column(Text, nullable=True)
    distance_poll_mover_to_site = Column(Text, nullable=True)
    driver_contact_name = Column(Text, nullable=True)
    driver_contact_number = Column(Text, nullable=True)
    delivery_type_a_to_b = Column(Text, nullable=True)
    transportation_time = Column(Text, nullable=True)
    estimate_depart_from_start_point_etd = Column(Text, nullable=True)
    estimate_arrive_sites_time_eta = Column(Text, nullable=True)
    lsp_tracker = Column(Text, nullable=True)
    hw_tracker = Column(Text, nullable=True)
    actual_depart_from_start_point_atd = Column(Text, nullable=True)
    actual_arrive_time_ata = Column(Text, nullable=True)
    subcon = Column(Text, nullable=True)
    subcon_receiver_contact_number = Column(Text, nullable=True)
    status_delivery = Column(Text, nullable=True)
    issue_remark = Column(Text, nullable=True)
    mos_attempt_1st_time = Column(Text, nullable=True)
    mos_attempt_2nd_time = Column(Text, nullable=True)
    mos_attempt_3rd_time = Column(Text, nullable=True)
    mos_attempt_4th_time = Column(Text, nullable=True)
    mos_attempt_5th_time = Column(Text, nullable=True)
    mos_attempt_6th_time = Column(Text, nullable=True)
    mos_type = Column(Text, nullable=True)
    region = Column(Text, nullable=True)
    plan_mos_date = Column(Text, nullable=True)
    last_updated_by = Column(Text, nullable=True)
    gs_sheet = Column(Text, nullable=True)
    gs_row = Column(Integer, nullable=True)


class DNRecord(Base):
    __tablename__ = "dn_record"
    id = Column(Integer, primary_key=True, index=True)
    dn_number = Column(String(64), index=True, nullable=False)
    du_id = Column(String(32), nullable=True)
    status = Column(String(64), nullable=False)
    remark = Column(Text, nullable=True)
    photo_url = Column(Text, nullable=True)
    lng = Column(String(20), nullable=True)
    lat = Column(String(20), nullable=True)
    updated_by = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DNSyncLog(Base):
    __tablename__ = "dn_sync_log"
    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(16), nullable=False)
    synced_count = Column(Integer, nullable=False, default=0)
    dn_numbers_json = Column(Text, nullable=True)
    message = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    error_traceback = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    @property
    def dn_numbers(self) -> list[str]:
        if not self.dn_numbers_json:
            return []
        try:
            data = json.loads(self.dn_numbers_json)
        except Exception:
            return []
        if isinstance(data, list):
            return data
        return []


class StatusDeliveryLspStat(Base):
    __tablename__ = "status_delivery_lsp_stat"
    __table_args__ = (
        UniqueConstraint(
            "lsp",
            "recorded_at",
            name="uq_status_delivery_lsp_stat_lsp_recorded_at",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    lsp = Column(String(128), nullable=False, index=True)
    total_dn = Column(Integer, nullable=False)
    status_not_empty = Column(Integer, nullable=False)
    plan_mos_date = Column(String(32), nullable=False)
    recorded_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
