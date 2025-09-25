import json
from sqlalchemy import Column, String, Integer, DateTime, Text
from sqlalchemy.sql import func
from .db import Base

class DU(Base):
    __tablename__ = "du"
    id = Column(Integer, primary_key=True, index=True)
    du_id = Column(String(32), unique=True, index=True, nullable=False)

class DURecord(Base):
    __tablename__ = "du_record"
    id = Column(Integer, primary_key=True, index=True)
    du_id = Column(String(32), index=True, nullable=False)
    status = Column(String(64), nullable=False)
    remark = Column(Text, nullable=True)
    photo_url = Column(Text, nullable=True)
    lng = Column(String(20), nullable=True) #经度
    lat = Column(String(20), nullable=True) #纬度
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DN(Base):
    __tablename__ = "dn"
    id = Column(Integer, primary_key=True, index=True)
    dn_number = Column(String(64), unique=True, index=True, nullable=False)
    du_id = Column(String(64), nullable=True)
    status = Column(String(64), nullable=True)
    remark = Column(Text, nullable=True)
    photo_url = Column(Text, nullable=True)
    lng = Column(String(20), nullable=True)
    lat = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status_wh = Column(String(64), nullable=True)
    lsp = Column(String(128), nullable=True)
    area = Column(String(64), nullable=True)
    mos_given_time = Column(String(64), nullable=True)
    expected_arrival_time_from_project = Column(String(64), nullable=True)
    project_request = Column(String(64), nullable=True)
    distance_poll_mover_to_site = Column(String(64), nullable=True)
    driver_contact_name = Column(String(128), nullable=True)
    driver_contact_number = Column(String(64), nullable=True)
    delivery_type_a_to_b = Column(String(64), nullable=True)
    transportation_time = Column(String(64), nullable=True)
    estimate_depart_from_start_point_etd = Column(String(64), nullable=True)
    estimate_arrive_sites_time_eta = Column(String(64), nullable=True)
    lsp_tracker = Column(String(128), nullable=True)
    hw_tracker = Column(String(128), nullable=True)
    actual_depart_from_start_point_atd = Column(String(64), nullable=True)
    actual_arrive_time_ata = Column(String(64), nullable=True)
    subcon = Column(String(128), nullable=True)
    subcon_receiver_contact_number = Column(String(128), nullable=True)
    status_delivery = Column(String(64), nullable=True)
    issue_remark = Column(Text, nullable=True)
    mos_attempt_1st_time = Column(String(64), nullable=True)
    mos_attempt_2nd_time = Column(String(64), nullable=True)
    mos_attempt_3rd_time = Column(String(64), nullable=True)
    mos_attempt_4th_time = Column(String(64), nullable=True)
    mos_attempt_5th_time = Column(String(64), nullable=True)
    mos_attempt_6th_time = Column(String(64), nullable=True)
    mos_type = Column(String(64), nullable=True)
    region = Column(String(64), nullable=True)
    plan_mos_date = Column(String(64), nullable=True)
    last_updated_by = Column(String(128), nullable=True)
    gs_sheet = Column(String(256), nullable=True)
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
