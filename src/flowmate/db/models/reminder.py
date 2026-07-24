from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from flowmate.db.base import Base
from flowmate.reminders.enums import ReminderScheduleKind, ReminderStatus, ReminderType
from flowmate.workspaces import WORKSPACE_VALUES, WorkspaceScoped

REMINDER_TYPES = tuple(value.value for value in ReminderType)
REMINDER_STATUSES = tuple(value.value for value in ReminderStatus)
REMINDER_SCHEDULE_KINDS = tuple(value.value for value in ReminderScheduleKind)


class Reminder(WorkspaceScoped, Base):
    __tablename__ = "reminders"
    __table_args__ = (
        CheckConstraint(
            f"type IN {REMINDER_TYPES!r}",
            name="ck_reminders_type",
        ),
        CheckConstraint(
            f"status IN {REMINDER_STATUSES!r}",
            name="ck_reminders_status",
        ),
        CheckConstraint(
            f"schedule_kind IN {REMINDER_SCHEDULE_KINDS!r}",
            name="ck_reminders_schedule_kind",
        ),
        CheckConstraint(
            f"workspace IN {WORKSPACE_VALUES!r}",
            name="ck_reminders_workspace",
        ),
        CheckConstraint(
            "schedule_kind = 'manual' OR reference_at IS NOT NULL",
            name="ck_reminders_managed_reference",
        ),
        CheckConstraint(
            "delivery_attempts >= 0",
            name="ck_reminders_delivery_attempts_nonnegative",
        ),
        CheckConstraint(
            "char_length(btrim(deduplication_key)) > 0",
            name="ck_reminders_deduplication_key_not_blank",
        ),
        CheckConstraint(
            "message IS NULL OR char_length(btrim(message)) > 0",
            name="ck_reminders_message_not_blank",
        ),
        UniqueConstraint(
            "user_id",
            "workspace",
            "deduplication_key",
            name="uq_reminders_user_workspace_deduplication_key",
        ),
        UniqueConstraint(
            "user_id",
            "workspace",
            "type",
            "digest_local_date",
            name="uq_reminders_user_workspace_digest_local_date",
        ),
        Index("ix_reminders_status_scheduled_at", "status", "scheduled_at"),
        Index(
            "ix_reminders_user_workspace_status",
            "user_id",
            "workspace",
            "status",
        ),
        Index("ix_reminders_work_item_id", "work_item_id"),
        Index(
            "ix_reminders_work_item_status_kind",
            "work_item_id",
            "status",
            "schedule_kind",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reference_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    schedule_kind: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=ReminderScheduleKind.MANUAL.value,
        server_default=ReminderScheduleKind.MANUAL.value,
    )
    digest_local_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    schedule_timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ReminderStatus.PENDING.value,
        server_default=ReminderStatus.PENDING.value,
    )
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processing_token: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    delivery_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    delivery_unknown_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivery_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
