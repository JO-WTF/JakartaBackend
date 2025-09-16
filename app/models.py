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
    status = Column(String(20), nullable=False)
    remark = Column(Text, nullable=True)
    photo_url = Column(Text, nullable=True)
    lng = Column(String(20), nullable=True) #经度
    lat = Column(String(20), nullable=True) #纬度
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
