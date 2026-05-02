import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, Numeric, ForeignKey, JSON,
    BigInteger, LargeBinary,
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
    meta_audience_id = Column(String, nullable=True)
    meta_lookalike_id = Column(String, nullable=True)
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


class AdAccount(Base):
    __tablename__ = "ad_accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(50), nullable=False, unique=True)
    account_name = Column(String(255), nullable=False)
    meta_access_token = Column(Text, nullable=True)
    notification_email = Column(String(255), nullable=True)
    audit_cron = Column(String(50), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_audit_at = Column(DateTime, nullable=True)
    currency = Column(String(10), nullable=True)
    timezone_name = Column(String(100), nullable=True)
    website_url = Column(Text, nullable=True)
    business_profile = Column(JSON, nullable=True, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class AuditReport(Base):
    __tablename__ = "audit_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String(50), nullable=False)
    generated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    raw_metrics = Column(JSON, nullable=True)
    analyses = Column(JSON, nullable=False, default=dict)
    total_spend_7d = Column(Numeric(12, 2), nullable=True)
    total_spend_30d = Column(Numeric(12, 2), nullable=True)
    total_conversions_7d = Column(Integer, nullable=True)
    total_conversions_30d = Column(Integer, nullable=True)
    total_impressions_7d = Column(BigInteger, nullable=True)
    total_impressions_30d = Column(BigInteger, nullable=True)
    total_clicks_7d = Column(Integer, nullable=True)
    total_clicks_30d = Column(Integer, nullable=True)
    avg_cpa_30d = Column(Numeric(10, 2), nullable=True)
    avg_ctr_30d = Column(Numeric(6, 3), nullable=True)
    avg_roas_30d = Column(Numeric(10, 2), nullable=True)
    campaign_count = Column(Integer, nullable=True)
    audience_count = Column(Integer, nullable=True)
    pdf_report = Column(LargeBinary, nullable=True)
    pdf_filename = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False, default="in_progress")
    error_message = Column(Text, nullable=True)
    models_used = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


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
