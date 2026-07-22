from datetime import datetime, time
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from flowmate.db.base import Base


class UserNotificationPreferences(Base):
    __tablename__ = "user_notification_preferences"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(timezone)) > 0",
            name="ck_user_notification_preferences_timezone_not_blank",
        ),
        CheckConstraint(
            "default_snooze_minutes BETWEEN 1 AND 10080",
            name="ck_user_notification_preferences_snooze_range",
        ),
        CheckConstraint(
            "quiet_hours_start <> quiet_hours_end",
            name="ck_user_notification_preferences_quiet_range",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    morning_digest_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    morning_digest_time: Mapped[time] = mapped_column(Time(), nullable=False)
    evening_digest_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    evening_digest_time: Mapped[time] = mapped_column(Time(), nullable=False)
    quiet_hours_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    quiet_hours_start: Mapped[time] = mapped_column(Time(), nullable=False)
    quiet_hours_end: Mapped[time] = mapped_column(Time(), nullable=False)
    default_snooze_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    send_empty_digests: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
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
