import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, Numeric, ForeignKey, JSON
)
from database import Base


class SyncStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"


class SyncConfig(Base):
    __tablename__ = "sync_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ghl_ltv_field_key = Column(String, nullable=False)
    ghl_ltv_field_name = Column(String, nullable=False)
    meta_ad_account_id = Column(String, nullable=False)
    sync_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    config_id = Column(Integer, ForeignKey("sync_configs.id"), nullable=False)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    status = Column(String, default=SyncStatus.RUNNING)
    contacts_processed = Column(Integer, default=0)
    contacts_matched = Column(Integer, default=0)
    meta_audience_id = Column(String, nullable=True)
    meta_audience_name = Column(String, nullable=True)
    meta_lookalike_id = Column(String, nullable=True)
    meta_lookalike_name = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    normalization_stats = Column(JSON, nullable=True)


class SyncContact(Base):
    __tablename__ = "sync_contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sync_run_id = Column(Integer, ForeignKey("sync_runs.id"), nullable=False)
    ghl_contact_id = Column(String, nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    raw_ltv = Column(Numeric, default=0)
    normalized_value = Column(Integer, default=0)
    meta_matched = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
