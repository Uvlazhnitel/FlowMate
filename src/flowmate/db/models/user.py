from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from flowmate.db.base import Base
from flowmate.workspaces import WORKSPACE_VALUES, Workspace


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", name="users_telegram_user_id_key"),
        CheckConstraint(
            "telegram_user_id IS NULL OR telegram_user_id > 0",
            name="ck_users_telegram_user_id_positive",
        ),
        CheckConstraint(
            f"active_workspace IN {WORKSPACE_VALUES!r}",
            name="ck_users_active_workspace",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    active_workspace: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=Workspace.PERSONAL.value,
        server_default=Workspace.PERSONAL.value,
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
