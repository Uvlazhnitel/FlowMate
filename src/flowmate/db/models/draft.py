from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowmate.db.base import Base

OPEN_DRAFT_STATUSES = ("parsing", "needs_clarification", "ready")
DRAFT_STATUSES = (*OPEN_DRAFT_STATUSES, "confirmed", "cancelled", "expired", "failed")


class DraftSession(Base):
    __tablename__ = "draft_sessions"
    __table_args__ = (
        CheckConstraint(
            f"status IN {DRAFT_STATUSES!r}",
            name="ck_draft_sessions_status",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_draft_sessions_expiration",
        ),
        Index("ix_draft_sessions_user_status", "user_id", "status"),
        Index("ix_draft_sessions_expires_at", "expires_at"),
        Index(
            "uq_draft_sessions_user_open",
            "user_id",
            unique=True,
            postgresql_where=text(
                "meeting_id IS NULL AND "
                "status IN ('parsing', 'needs_clarification', 'ready')"
            ),
        ),
        UniqueConstraint(
            "meeting_id",
            "capture_sequence",
            name="uq_draft_sessions_meeting_capture_sequence",
        ),
        CheckConstraint(
            "capture_sequence IS NULL OR capture_sequence > 0",
            name="ck_draft_sessions_capture_sequence_positive",
        ),
        CheckConstraint(
            "capture_review_status IS NULL OR capture_review_status IN "
            "('pending', 'edited', 'removed')",
            name="ck_draft_sessions_capture_review_status",
        ),
        CheckConstraint(
            "overall_confidence IS NULL OR "
            "(overall_confidence >= 0 AND overall_confidence <= 1)",
            name="ck_draft_sessions_overall_confidence",
        ),
        CheckConstraint(
            "(meeting_id IS NULL AND capture_sequence IS NULL AND "
            "capture_review_status IS NULL) OR "
            "(meeting_id IS NOT NULL AND capture_sequence IS NOT NULL AND "
            "capture_review_status IS NOT NULL)",
            name="ck_draft_sessions_capture_fields",
        ),
        Index("ix_draft_sessions_meeting_capture", "meeting_id", "capture_sequence"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_note_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("notes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    meeting_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("meetings.id", ondelete="CASCADE")
    )
    capture_sequence: Mapped[int | None] = mapped_column(Integer)
    capture_review_status: Mapped[str | None] = mapped_column(String(16))
    capture_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    overall_confidence: Mapped[float | None] = mapped_column(Float)
    prompt_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft-v2", server_default="legacy-v1"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    analysis_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    current_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_question_options: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    current_question_context: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    current_question_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    processed_update_ids: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    processing_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    items: Mapped[list["DraftItemRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="DraftItemRecord.position",
    )


class DraftItemRecord(Base):
    __tablename__ = "draft_items"
    __table_args__ = (
        UniqueConstraint(
            "draft_session_id",
            "position",
            name="uq_draft_items_session_position",
        ),
        CheckConstraint("position > 0", name="ck_draft_items_position_positive"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_draft_items_confidence",
        ),
        CheckConstraint(
            "char_length(btrim(title)) > 0",
            name="ck_draft_items_title_not_blank",
        ),
        CheckConstraint(
            "item_type IN ('task', 'follow_up', 'waiting', 'question', 'note', "
            "'decision', 'agenda_item', 'unknown')",
            name="ck_draft_items_type",
        ),
        CheckConstraint(
            "readiness IN ('ready', 'clarification_required', 'unresolved')",
            name="ck_draft_items_readiness",
        ),
        CheckConstraint(
            "selected_priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_draft_items_selected_priority",
        ),
        Index("ix_draft_items_session", "draft_session_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    draft_session_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("draft_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    people_candidates: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    topic_candidates: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    original_date_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    selected_topic_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("topics.id", ondelete="SET NULL"),
        nullable=True,
    )
    selected_priority: Mapped[str] = mapped_column(
        String(16), nullable=False, default="normal", server_default="normal"
    )
    notes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    missing_fields: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    ambiguities: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    readiness: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    session: Mapped[DraftSession] = relationship(back_populates="items")


class DraftItemPerson(Base):
    __tablename__ = "draft_item_people"
    __table_args__ = (
        UniqueConstraint(
            "draft_item_id", "person_id", name="uq_draft_item_people_item_person"
        ),
        Index("ix_draft_item_people_user_id", "user_id"),
        Index("ix_draft_item_people_person_id", "person_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("draft_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
