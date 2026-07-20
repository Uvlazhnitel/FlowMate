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
        CheckConstraint(
            "telegram_update_id > 0",
            name="ck_notes_telegram_update_id_positive",
        ),
        CheckConstraint(
            "source IN ('text', 'voice')",
            name="ck_notes_source",
        ),
        CheckConstraint(
            "char_length(btrim(content)) > 0",
            name="ck_notes_content_not_blank",
        ),
        Index("ix_notes_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    telegram_update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
