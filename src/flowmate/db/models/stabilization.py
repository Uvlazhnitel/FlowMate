from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from flowmate.db.base import Base


class TelegramOperationReceipt(Base):
    __tablename__ = "telegram_operation_receipts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('processing','completed','retryable_failed')",
            name="ck_telegram_operation_receipts_status",
        ),
        CheckConstraint(
            "attempt_count > 0", name="ck_telegram_operation_receipts_attempts"
        ),
        Index("ix_telegram_receipts_status_lease", "status", "lease_expires_at"),
    )

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    event_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AIProcessingJob(Base):
    __tablename__ = "ai_processing_jobs"
    __table_args__ = (
        CheckConstraint(
            "job_kind IN ('draft_parse','draft_refine','meeting_capture_parse',"
            "'meeting_review_generate')",
            name="ck_ai_processing_jobs_kind",
        ),
        CheckConstraint(
            "status IN ('pending','processing','completed','failed')",
            name="ck_ai_processing_jobs_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_ai_processing_jobs_attempts"),
        UniqueConstraint(
            "job_kind", "entity_id", "operation_key", name="uq_ai_processing_job"
        ),
        Index("ix_ai_processing_jobs_due", "status", "next_attempt_at"),
        Index("ix_ai_processing_jobs_lease", "status", "lease_expires_at"),
        Index("ix_ai_processing_jobs_user", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    operation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    prompt_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    input_text: Mapped[str | None] = mapped_column(Text)
    input_source: Mapped[str | None] = mapped_column(String(16))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(
            "actor_kind IN ('telegram','pwa','system','operator')",
            name="ck_audit_events_actor_kind",
        ),
        CheckConstraint(
            "outcome IN ('success','rejected','failed','recovered')",
            name="ck_audit_events_outcome",
        ),
        Index("ix_audit_events_user_created", "user_id", "created_at", "id"),
        Index("ix_audit_events_action_created", "action", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    # Keep the immutable actor identifier even if a user is later removed.
    user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_kind: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
