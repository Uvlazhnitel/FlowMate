from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
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
from flowmate.meetings.enums import MeetingEventType, MeetingStatus, MeetingType

MEETING_TYPES = tuple(value.value for value in MeetingType)
MEETING_STATUSES = tuple(value.value for value in MeetingStatus)
MEETING_EVENT_TYPES = tuple(value.value for value in MeetingEventType)


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint(f"type IN {MEETING_TYPES!r}", name="ck_meetings_type"),
        CheckConstraint(f"status IN {MEETING_STATUSES!r}", name="ck_meetings_status"),
        CheckConstraint("char_length(btrim(title)) > 0", name="ck_meetings_title"),
        CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name="ck_meetings_time_order",
        ),
        CheckConstraint(
            "status != 'planned' OR (started_at IS NULL AND ended_at IS NULL)",
            name="ck_meetings_planned_times",
        ),
        CheckConstraint(
            "status != 'active' OR (started_at IS NOT NULL AND ended_at IS NULL)",
            name="ck_meetings_active_times",
        ),
        CheckConstraint(
            "status != 'completed' OR "
            "(started_at IS NOT NULL AND ended_at IS NOT NULL)",
            name="ck_meetings_completed_times",
        ),
        Index("ix_meetings_user_status", "user_id", "status"),
        Index("ix_meetings_user_started", "user_id", "started_at"),
        Index(
            "uq_meetings_user_active",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=MeetingStatus.PLANNED.value
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    primary_topic_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("topics.id", ondelete="SET NULL")
    )
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"
    __table_args__ = (
        UniqueConstraint("meeting_id", "person_id", name="uq_meeting_participants"),
        Index("ix_meeting_participants_user", "user_id"),
        Index("ix_meeting_participants_person", "person_id"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("people.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MeetingTopic(Base):
    __tablename__ = "meeting_topics"
    __table_args__ = (
        UniqueConstraint("meeting_id", "topic_id", name="uq_meeting_topics"),
        Index("ix_meeting_topics_user", "user_id"),
        Index("ix_meeting_topics_topic", "topic_id"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    topic_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MeetingNote(Base):
    __tablename__ = "meeting_notes"
    __table_args__ = (
        UniqueConstraint("note_id", name="uq_meeting_notes_note"),
        Index("ix_meeting_notes_meeting", "meeting_id"),
        Index("ix_meeting_notes_user", "user_id"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    note_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("notes.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MeetingEvent(Base):
    __tablename__ = "meeting_events"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN {MEETING_EVENT_TYPES!r}", name="ck_meeting_events_type"
        ),
        CheckConstraint(
            "num_nonnulls(telegram_update_id, client_action_id) <= 1",
            name="ck_meeting_events_one_origin",
        ),
        UniqueConstraint(
            "user_id",
            "telegram_update_id",
            "event_type",
            name="uq_meeting_events_telegram",
        ),
        UniqueConstraint(
            "user_id", "client_action_id", "event_type", name="uq_meeting_events_client"
        ),
        Index("ix_meeting_events_meeting_created", "meeting_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(32))
    new_status: Mapped[str] = mapped_column(String(32), nullable=False)
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger)
    client_action_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )


class MeetingSetupSession(Base):
    __tablename__ = "meeting_setup_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'completed', 'cancelled', 'expired')",
            name="ck_meeting_setup_sessions_status",
        ),
        CheckConstraint(
            "expires_at > created_at", name="ck_meeting_setup_sessions_expiration"
        ),
        Index(
            "uq_meeting_setup_sessions_user_open",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_meeting_setup_sessions_expires", "expires_at"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("meetings.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    step: Mapped[str] = mapped_column(String(32), nullable=False, default="type")
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    prompt_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MeetingReview(Base):
    __tablename__ = "meeting_reviews"
    __table_args__ = (
        UniqueConstraint("meeting_id", name="uq_meeting_reviews_meeting"),
        CheckConstraint(
            "status IN ('processing','review_required','completed','failed')",
            name="ck_meeting_reviews_status",
        ),
        Index("ix_meeting_reviews_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="processing"
    )
    summary: Mapped[str | None] = mapped_column(Text)
    suggested_next_actions: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    current_item_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    current_question: Mapped[str | None] = mapped_column(Text)
    current_question_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    current_question_message_id: Mapped[int | None] = mapped_column(BigInteger)
    processed_update_ids: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    generation_attempts: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default="0"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MeetingReviewItem(Base):
    __tablename__ = "meeting_review_items"
    __table_args__ = (
        UniqueConstraint(
            "review_id", "position", name="uq_meeting_review_items_position"
        ),
        CheckConstraint(
            "origin IN ('capture','existing_agenda','manual_review')",
            name="ck_meeting_review_items_origin",
        ),
        CheckConstraint(
            "category IN ('task','follow_up','waiting','answered_question',"
            "'unresolved_question','note','decision','agenda_item')",
            name="ck_meeting_review_items_category",
        ),
        CheckConstraint(
            "status IN ('pending','ready','clarification_required','excluded',"
            "'inbox','converted')",
            name="ck_meeting_review_items_status",
        ),
        CheckConstraint(
            "origin != 'capture' OR source_capture_id IS NOT NULL",
            name="ck_meeting_review_items_capture_source",
        ),
        Index("ix_meeting_review_items_review_status", "review_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("meeting_reviews.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_capture_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("draft_sessions.id", ondelete="CASCADE")
    )
    source_draft_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("draft_items.id", ondelete="SET NULL")
    )
    source_work_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("work_items.id", ondelete="SET NULL")
    )
    position: Mapped[int] = mapped_column(nullable=False)
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    suggested_next_action: Mapped[str | None] = mapped_column(Text)
    consequences: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    related_positions: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    clarification_question: Mapped[str | None] = mapped_column(Text)
    clarification_answer: Mapped[str | None] = mapped_column(Text)
    planner_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    result_work_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("work_items.id", ondelete="SET NULL")
    )
    result_note_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("notes.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class MeetingWorkItem(Base):
    __tablename__ = "meeting_work_items"
    __table_args__ = (
        UniqueConstraint("meeting_id", "work_item_id", name="uq_meeting_work_items"),
        CheckConstraint(
            "role IN ('result','decision','agenda')", name="ck_meeting_work_items_role"
        ),
        Index("ix_meeting_work_items_work_item", "work_item_id"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    review_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("meeting_review_items.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MeetingAgendaEntry(Base):
    __tablename__ = "meeting_agenda_entries"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id", "work_item_id", name="uq_meeting_agenda_entries"
        ),
        CheckConstraint(
            "outcome IN ('pending','discussed','answered','deferred','unresolved')",
            name="ck_meeting_agenda_entries_outcome",
        ),
        Index("ix_meeting_agenda_entries_meeting", "meeting_id", "outcome"),
    )
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("meetings.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
