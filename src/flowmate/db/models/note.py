from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from flowmate.db.base import Base


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        UniqueConstraint(
            "telegram_update_id",
            name="notes_telegram_update_id_key",
        ),
        UniqueConstraint(
            "source_draft_item_id",
            name="uq_notes_source_draft_item_id",
        ),
        CheckConstraint(
            "(source IN ('text', 'voice') AND telegram_update_id > 0) OR "
            "(source = 'manual' AND telegram_update_id IS NULL)",
            name="ck_notes_source_update_consistency",
        ),
        CheckConstraint(
            "source IN ('text', 'voice', 'manual')",
            name="ck_notes_source",
        ),
        CheckConstraint(
            "(content IS NOT NULL AND char_length(btrim(content)) > 0 AND "
            "transcript_redacted_at IS NULL) OR "
            "(content IS NULL AND source = 'voice' AND "
            "transcript_redacted_at IS NOT NULL)",
            name="ck_notes_content_or_redacted",
        ),
        CheckConstraint(
            "inbox_disposition IN ('pending', 'kept', 'archived')",
            name="ck_notes_inbox_disposition",
        ),
        Index("ix_notes_user_id_created_at", "user_id", "created_at"),
        Index(
            "ix_notes_user_inbox_disposition",
            "user_id",
            "inbox_disposition",
            "created_at",
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
    content: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    inbox_disposition: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    telegram_update_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_draft_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey(
            "draft_items.id",
            name="fk_notes_source_draft_item_id_draft_items",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    transcript_redacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
