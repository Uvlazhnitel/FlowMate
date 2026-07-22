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
from flowmate.task_engine.enums import (
    WorkItemAction,
    WorkItemEventType,
    WorkItemPriority,
    WorkItemRelationType,
    WorkItemStatus,
    WorkItemType,
)

WORK_ITEM_TYPES = tuple(value.value for value in WorkItemType)
WORK_ITEM_STATUSES = tuple(value.value for value in WorkItemStatus)
WORK_ITEM_PRIORITIES = tuple(value.value for value in WorkItemPriority)
WORK_ITEM_RELATION_TYPES = tuple(value.value for value in WorkItemRelationType)
WORK_ITEM_EVENT_TYPES = tuple(value.value for value in WorkItemEventType)
WORK_ITEM_ACTIONS = tuple(value.value for value in WorkItemAction)


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(name)) > 0",
            name="ck_topics_name_not_blank",
        ),
        Index("ix_topics_user_active", "user_id", "is_active"),
        Index(
            "uq_topics_user_normalized_name",
            "user_id",
            text("lower(btrim(name))"),
            unique=True,
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
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliases: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
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


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(display_name)) > 0",
            name="ck_people_display_name_not_blank",
        ),
        Index("ix_people_user_active", "user_id", "is_active"),
        Index(
            "ix_people_user_normalized_name",
            "user_id",
            text("lower(btrim(display_name))"),
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
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliases: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
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


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (
        UniqueConstraint(
            "source_draft_item_id",
            name="uq_work_items_source_draft_item_id",
        ),
        CheckConstraint(
            f"type IN {WORK_ITEM_TYPES!r}",
            name="ck_work_items_type",
        ),
        CheckConstraint(
            f"status IN {WORK_ITEM_STATUSES!r}",
            name="ck_work_items_status",
        ),
        CheckConstraint(
            f"priority IN {WORK_ITEM_PRIORITIES!r}",
            name="ck_work_items_priority",
        ),
        CheckConstraint(
            "char_length(btrim(title)) > 0",
            name="ck_work_items_title_not_blank",
        ),
        Index("ix_work_items_user_status", "user_id", "status"),
        Index("ix_work_items_user_due_at", "user_id", "due_at"),
        Index("ix_work_items_topic_id", "topic_id"),
        Index("ix_work_items_source_note_id", "source_note_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=WorkItemStatus.INBOX.value,
        server_default=WorkItemStatus.INBOX.value,
    )
    priority: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=WorkItemPriority.NORMAL.value,
        server_default=WorkItemPriority.NORMAL.value,
    )
    topic_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("topics.id", ondelete="SET NULL"),
        nullable=True,
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_follow_up_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    waiting_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_note_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("notes.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_draft_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("draft_items.id", ondelete="SET NULL"),
        nullable=True,
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


class WorkItemPerson(Base):
    __tablename__ = "work_item_people"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id",
            "person_id",
            name="uq_work_item_people_item_person",
        ),
        CheckConstraint(
            "role IS NULL OR char_length(btrim(role)) > 0",
            name="ck_work_item_people_role_not_blank",
        ),
        Index("ix_work_item_people_user_id", "user_id"),
        Index("ix_work_item_people_person_id", "person_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkItemRelation(Base):
    __tablename__ = "work_item_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_work_item_id",
            "target_work_item_id",
            "relation_type",
            name="uq_work_item_relations_source_target_type",
        ),
        CheckConstraint(
            "source_work_item_id <> target_work_item_id",
            name="ck_work_item_relations_not_self",
        ),
        CheckConstraint(
            f"relation_type IN {WORK_ITEM_RELATION_TYPES!r}",
            name="ck_work_item_relations_type",
        ),
        Index("ix_work_item_relations_user_source", "user_id", "source_work_item_id"),
        Index("ix_work_item_relations_user_target", "user_id", "target_work_item_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_work_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_work_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NoteLink(Base):
    __tablename__ = "note_links"
    __table_args__ = (
        UniqueConstraint(
            "note_id",
            "work_item_id",
            name="uq_note_links_note_work_item",
        ),
        UniqueConstraint(
            "note_id",
            "person_id",
            name="uq_note_links_note_person",
        ),
        UniqueConstraint(
            "note_id",
            "topic_id",
            name="uq_note_links_note_topic",
        ),
        CheckConstraint(
            "num_nonnulls(work_item_id, person_id, topic_id) = 1",
            name="ck_note_links_one_target",
        ),
        Index("ix_note_links_user_note", "user_id", "note_id"),
        Index("ix_note_links_work_item_id", "work_item_id"),
        Index("ix_note_links_person_id", "person_id"),
        Index("ix_note_links_topic_id", "topic_id"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    note_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("notes.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=True,
    )
    person_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=True,
    )
    topic_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("topics.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkItemEvent(Base):
    __tablename__ = "work_item_events"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN {WORK_ITEM_EVENT_TYPES!r}",
            name="ck_work_item_events_type",
        ),
        Index(
            "ix_work_item_events_item_created_at",
            "work_item_id",
            "created_at",
        ),
        Index("ix_work_item_events_user_id", "user_id"),
        UniqueConstraint(
            "telegram_update_id",
            name="uq_work_item_events_telegram_update_id",
        ),
        UniqueConstraint(
            "user_id",
            "client_action_id",
            name="uq_work_item_events_user_client_action_id",
        ),
        CheckConstraint(
            "telegram_update_id IS NULL OR telegram_update_id > 0",
            name="ck_work_item_events_telegram_update_id_positive",
        ),
        CheckConstraint(
            "num_nonnulls(telegram_update_id, client_action_id) <= 1",
            name="ck_work_item_events_one_action_origin",
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
    work_item_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    client_action_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )


class WorkItemActionSession(Base):
    __tablename__ = "work_item_action_sessions"
    __table_args__ = (
        CheckConstraint(
            f"action IN {WORK_ITEM_ACTIONS!r}",
            name="ck_work_item_action_sessions_action",
        ),
        CheckConstraint(
            "status IN ('open', 'completed', 'cancelled', 'expired')",
            name="ck_work_item_action_sessions_status",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_work_item_action_sessions_expiration",
        ),
        CheckConstraint(
            "telegram_update_id IS NULL OR telegram_update_id > 0",
            name="ck_work_item_action_sessions_telegram_update_id_positive",
        ),
        UniqueConstraint(
            "telegram_update_id",
            name="uq_work_item_action_sessions_telegram_update_id",
        ),
        Index(
            "uq_work_item_action_sessions_user_open",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_work_item_action_sessions_expires_at", "expires_at"),
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
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", server_default="open"
    )
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    prompt_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
