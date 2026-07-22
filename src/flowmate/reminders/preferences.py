from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.core.config import Settings
from flowmate.db.models import Reminder, User, UserNotificationPreferences
from flowmate.reminders.enums import ReminderStatus, ReminderType
from flowmate.reminders.timezone import quiet_hours_end


@dataclass(frozen=True, slots=True)
class NotificationDefaults:
    timezone: str
    morning_digest_time: time
    evening_digest_time: time
    quiet_hours_start: time
    quiet_hours_end: time
    snooze_minutes: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "NotificationDefaults":
        return cls(
            timezone=settings.app_timezone,
            morning_digest_time=settings.default_morning_digest_time,
            evening_digest_time=settings.default_evening_digest_time,
            quiet_hours_start=settings.default_quiet_hours_start,
            quiet_hours_end=settings.default_quiet_hours_end,
            snooze_minutes=settings.default_snooze_minutes,
        )


@dataclass(frozen=True, slots=True)
class EffectiveNotificationPreferences:
    timezone: str
    morning_digest_enabled: bool
    morning_digest_time: time
    evening_digest_enabled: bool
    evening_digest_time: time
    quiet_hours_enabled: bool
    quiet_hours_start: time
    quiet_hours_end: time
    default_snooze_minutes: int
    send_empty_digests: bool

    @property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def validate_timezone(value: str) -> str:
    normalized = value.strip()
    try:
        ZoneInfo(normalized)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("timezone must be a valid IANA timezone") from error
    return normalized


def validate_clock_time(value: time) -> time:
    if value.tzinfo is not None:
        raise ValueError("notification time must not include timezone")
    return value.replace(second=0, microsecond=0)


def effective_preferences(
    value: UserNotificationPreferences | None,
    defaults: NotificationDefaults,
) -> EffectiveNotificationPreferences:
    if value is None:
        return EffectiveNotificationPreferences(
            timezone=defaults.timezone,
            morning_digest_enabled=False,
            morning_digest_time=defaults.morning_digest_time,
            evening_digest_enabled=False,
            evening_digest_time=defaults.evening_digest_time,
            quiet_hours_enabled=False,
            quiet_hours_start=defaults.quiet_hours_start,
            quiet_hours_end=defaults.quiet_hours_end,
            default_snooze_minutes=defaults.snooze_minutes,
            send_empty_digests=False,
        )
    return EffectiveNotificationPreferences(
        timezone=value.timezone,
        morning_digest_enabled=value.morning_digest_enabled,
        morning_digest_time=value.morning_digest_time,
        evening_digest_enabled=value.evening_digest_enabled,
        evening_digest_time=value.evening_digest_time,
        quiet_hours_enabled=value.quiet_hours_enabled,
        quiet_hours_start=value.quiet_hours_start,
        quiet_hours_end=value.quiet_hours_end,
        default_snooze_minutes=value.default_snooze_minutes,
        send_empty_digests=value.send_empty_digests,
    )


async def get_notification_preferences(
    session: AsyncSession,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> UserNotificationPreferences | None:
    statement = select(UserNotificationPreferences).where(
        UserNotificationPreferences.user_id == user_id
    )
    if for_update:
        statement = statement.with_for_update()
    return cast(UserNotificationPreferences | None, await session.scalar(statement))


async def get_effective_notification_preferences(
    session: AsyncSession,
    user_id: UUID,
    defaults: NotificationDefaults,
) -> EffectiveNotificationPreferences:
    return effective_preferences(
        await get_notification_preferences(session, user_id), defaults
    )


async def get_or_create_notification_preferences(
    session: AsyncSession,
    user_id: UUID,
    defaults: NotificationDefaults,
) -> UserNotificationPreferences:
    owned_user = await session.scalar(
        select(User.id).where(User.id == user_id, User.is_active.is_(True))
    )
    if owned_user is None:
        raise ValueError("user not found")
    statement = (
        insert(UserNotificationPreferences)
        .values(
            user_id=user_id,
            timezone=defaults.timezone,
            morning_digest_enabled=False,
            morning_digest_time=defaults.morning_digest_time,
            evening_digest_enabled=False,
            evening_digest_time=defaults.evening_digest_time,
            quiet_hours_enabled=False,
            quiet_hours_start=defaults.quiet_hours_start,
            quiet_hours_end=defaults.quiet_hours_end,
            default_snooze_minutes=defaults.snooze_minutes,
            send_empty_digests=False,
        )
        .on_conflict_do_nothing(index_elements=["user_id"])
        .returning(UserNotificationPreferences)
    )
    created = (await session.execute(statement)).scalar_one_or_none()
    if created is not None:
        return created
    existing = await get_notification_preferences(session, user_id, for_update=True)
    if existing is None:
        raise RuntimeError("notification preferences could not be loaded")
    return existing


def parse_time(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("time must use HH:MM")
    try:
        hour, minute = (int(part) for part in parts)
        return time(hour, minute)
    except ValueError as error:
        raise ValueError("time must use HH:MM") from error


def quiet_hours_deferred_until(
    reminder_id: UUID,
    *,
    now: datetime,
    preferences: EffectiveNotificationPreferences,
) -> datetime | None:
    if not preferences.quiet_hours_enabled:
        return None
    quiet_end = quiet_hours_end(
        now,
        timezone=preferences.zoneinfo,
        start=preferences.quiet_hours_start,
        end=preferences.quiet_hours_end,
    )
    if quiet_end is None:
        return None
    jitter_seconds = int.from_bytes(reminder_id.bytes[:2]) % 120
    return quiet_end + timedelta(seconds=jitter_seconds)


async def defer_due_reminders_for_quiet_hours(
    session: AsyncSession,
    *,
    now: datetime,
    defaults: NotificationDefaults,
    limit: int,
) -> int:
    from flowmate.reminders.service import validate_aware_datetime

    validate_aware_datetime(now, "now")
    pending_due = and_(
        Reminder.status == ReminderStatus.PENDING.value,
        func.coalesce(Reminder.next_attempt_at, Reminder.scheduled_at) <= now,
    )
    snoozed_due = and_(
        Reminder.status == ReminderStatus.SNOOZED.value,
        Reminder.snoozed_until <= now,
    )
    rows = await session.execute(
        select(Reminder, UserNotificationPreferences)
        .outerjoin(
            UserNotificationPreferences,
            UserNotificationPreferences.user_id == Reminder.user_id,
        )
        .where(
            or_(pending_due, snoozed_due),
            Reminder.type.not_in(
                [
                    ReminderType.MORNING_DIGEST.value,
                    ReminderType.EVENING_DIGEST.value,
                ]
            ),
        )
        .order_by(Reminder.scheduled_at, Reminder.created_at)
        .limit(limit)
        .with_for_update(of=Reminder, skip_locked=True)
    )
    deferred = 0
    for reminder, stored in rows:
        preferences = effective_preferences(stored, defaults)
        deferred_until = quiet_hours_deferred_until(
            reminder.id,
            now=now,
            preferences=preferences,
        )
        if deferred_until is None:
            continue
        reminder.status = ReminderStatus.SNOOZED.value
        reminder.snoozed_until = deferred_until
        reminder.next_attempt_at = None
        reminder.processing_started_at = None
        reminder.processing_token = None
        deferred += 1
    await session.flush()
    return deferred
