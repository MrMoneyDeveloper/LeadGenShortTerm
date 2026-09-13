from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def now():
    return datetime.now(UTC)


def uid():
    return str(uuid4())


class SourceCursor(Base):
    __tablename__ = "source_cursors"
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    cursor: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PipelineState(Base):
    __tablename__ = "pipeline_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    paused: Mapped[bool] = mapped_column(Boolean, default=True)
    draining: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    storage_pressure: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    export_next_position: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    export_spreadsheet_id: Mapped[str] = mapped_column(String(160), default="", server_default="")
    export_folder_id: Mapped[str] = mapped_column(String(160), default="", server_default="")
    export_shard_rows: Mapped[int] = mapped_column(Integer, default=5000, server_default="5000")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    kind: Mapped[str] = mapped_column(String(24))
    source: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    batch_size: Mapped[int] = mapped_column(Integer)
    cursor_in: Mapped[dict | None] = mapped_column(JSONB)
    cursor_out: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceRecord(Base):
    __tablename__ = "source_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_record_id: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    identity_hash: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    __table_args__ = (UniqueConstraint("source", "source_record_id"),)


class Dedupe(Base):
    __tablename__ = "dedupe_index"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Identity(Base):
    __tablename__ = "identity_index"
    identity_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    email_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Candidate(Base):
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    record_id: Mapped[str | None] = mapped_column(ForeignKey("source_records.id", ondelete="SET NULL"), unique=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    identity_hash: Mapped[str] = mapped_column(String(64), index=True)
    excerpt: Mapped[str] = mapped_column(String(2000))
    contacts: Mapped[list] = mapped_column(JSONB, default=list)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    rank_score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    product_type: Mapped[str] = mapped_column(String(24))
    score: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(32), default="VALIDATE", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Classification(Base):
    __tablename__ = "classification_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(100))
    result: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("candidate_id", "provider"),)


class EmailValidation(Base):
    __tablename__ = "email_validation"
    email_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str] = mapped_column(String(100))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Lead(Base):
    __tablename__ = "validated_leads"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("candidates.id", ondelete="SET NULL"), index=True)
    profile: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    rank_score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    email: Mapped[str] = mapped_column(String(320))
    email_hash: Mapped[str] = mapped_column(String(64), unique=True)
    identity_hash: Mapped[str] = mapped_column(String(64), unique=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    email_source_url: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str] = mapped_column(String(2000))
    product_type: Mapped[str] = mapped_column(String(24))
    score: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(32), default="VALIDATED")
    validation_status: Mapped[str] = mapped_column(String(32), default="DELIVERABLE_DOMAIN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Metric(Base):
    __tablename__ = "pipeline_metrics"
    day: Mapped[datetime] = mapped_column(Date, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(Float, default=0)


class Usage(Base):
    __tablename__ = "api_usage"
    day: Mapped[datetime] = mapped_column(Date, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    units: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0)


class Failure(Base):
    __tablename__ = "failed_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source: Mapped[str | None] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ExportBatch(Base):
    """Immutable delivery until ACK; then retain only a compact receipt, never lead payloads."""

    __tablename__ = "export_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    campaign_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="CLAIMED", index=True)
    spreadsheet_id: Mapped[str] = mapped_column(String(160))
    drive_folder_id: Mapped[str] = mapped_column(String(160))
    checksum: Mapped[str] = mapped_column(String(64))
    fields: Mapped[list] = mapped_column(JSONB, default=list)
    items: Mapped[list] = mapped_column(JSONB, default=list)
    count: Mapped[int] = mapped_column(Integer)
    first_position: Mapped[int] = mapped_column(BigInteger)
    drive_file_id: Mapped[str | None] = mapped_column(String(160))
    ack_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_jobs_claim", Job.status, Job.available_at)
Index("ix_candidates_stage_due", Candidate.status, Candidate.available_at)
Index("ix_candidates_priority", Candidate.status, Candidate.rank_score.desc(), Candidate.available_at)
