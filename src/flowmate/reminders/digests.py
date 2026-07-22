# ruff: noqa: RUF001
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from flowmate.db.models import (
    Reminder,
    User,
    UserNotificationPreferences,
    WorkItem,
    WorkItemEvent,
)
from flowmate.reminders.enums import ReminderStatus, ReminderType
from flowmate.reminders.preferences import (
    EffectiveNotificationPreferences,
    NotificationDefaults,
    effective_preferences,
)
from flowmate.reminders.sync import ReminderPolicy, sync_work_item_reminders
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.enums import WorkItemStatus, WorkItemType
from flowmate.task_engine.queries import OPEN_STATUSES


@dataclass(frozen=True, slots=True)
class DigestSnapshot:
    overdue: int = 0
    due_today: int = 0
    follow_ups: int = 0
    waiting: int = 0
    questions: int = 0
    inbox: int = 0
    reschedule: int = 0

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.overdue,
                self.due_today,
                self.follow_ups,
                self.waiting,
                self.questions,
                self.inbox,
                self.reschedule,
            )
        )


async def _count(session: AsyncSession, *conditions: ColumnElement[bool]) -> int:
    statement = select(func.count(WorkItem.id)).where(*conditions)
    return int((await session.scalar(statement)) or 0)


async def build_digest_snapshot(
    session: AsyncSession,
    user_id: UUID,
    reminder_type: ReminderType,
    *,
    now: datetime,
    preferences: EffectiveNotificationPreferences,
) -> DigestSnapshot:
    timezone = preferences.zoneinfo
    local_now = now.astimezone(timezone)
    start = resolve_local_datetime(
        local_now.date(),
        local_now.replace(hour=0, minute=0, second=0, microsecond=0).time(),
        timezone,
    ).astimezone(UTC)
    end = resolve_local_datetime(
        local_now.date() + timedelta(days=1),
        local_now.replace(hour=0, minute=0, second=0, microsecond=0).time(),
        timezone,
    ).astimezone(UTC)
    owned_open = (
        WorkItem.user_id == user_id,
        WorkItem.status.in_(OPEN_STATUSES),
    )
    ordinary = WorkItem.type.not_in(
        [WorkItemType.FOLLOW_UP.value, WorkItemType.WAITING.value]
    )
    if reminder_type is ReminderType.MORNING_DIGEST:
        return DigestSnapshot(
            overdue=await _count(
                session, *owned_open, ordinary, WorkItem.due_at < start
            ),
            due_today=await _count(
                session,
                *owned_open,
                WorkItem.type == WorkItemType.TASK.value,
                WorkItem.due_at >= start,
                WorkItem.due_at < end,
            ),
            follow_ups=await _count(
                session,
                *owned_open,
                WorkItem.type == WorkItemType.FOLLOW_UP.value,
                WorkItem.next_follow_up_at >= start,
                WorkItem.next_follow_up_at < end,
            ),
            waiting=await _count(
                session,
                *owned_open,
                WorkItem.type == WorkItemType.WAITING.value,
                WorkItem.due_at < start,
            ),
            questions=await _count(
                session,
                *owned_open,
                WorkItem.type == WorkItemType.QUESTION.value,
            ),
            inbox=await _count(
                session,
                WorkItem.user_id == user_id,
                WorkItem.status == WorkItemStatus.INBOX.value,
            ),
        )
    due_today = await _count(
        session,
        *owned_open,
        ordinary,
        WorkItem.due_at >= start,
        WorkItem.due_at < end,
    )
    overdue_followups = await _count(
        session,
        *owned_open,
        WorkItem.type == WorkItemType.FOLLOW_UP.value,
        WorkItem.next_follow_up_at < now,
    )
    waiting = await _count(
        session,
        *owned_open,
        WorkItem.type == WorkItemType.WAITING.value,
        WorkItem.due_at < end,
    )
    return DigestSnapshot(
        due_today=due_today,
        follow_ups=overdue_followups,
        waiting=waiting,
        reschedule=due_today + overdue_followups,
    )


def format_digest_message(
    reminder_type: ReminderType,
    snapshot: DigestSnapshot,
) -> str:
    if reminder_type is ReminderType.MORNING_DIGEST:
        return (
            "Доброе утро\n\n"
            f"🔴 Просрочено: {snapshot.overdue}\n"
            f"🟠 На сегодня: {snapshot.due_today}\n"
            f"🔁 Follow-up: {snapshot.follow_ups}\n"
            f"⏳ Ждём ответа: {snapshot.waiting}\n"
            f"❓ Открытые вопросы: {snapshot.questions}\n"
            f"📥 Входящие: {snapshot.inbox}"
        )
    return (
        "Вечерний обзор\n\n"
        f"🟠 Не завершено сегодня: {snapshot.due_today}\n"
        f"🔁 Просроченные follow-up: {snapshot.follow_ups}\n"
        f"⏳ Требуют внимания: {snapshot.waiting}\n"
        f"📅 Можно перенести: {snapshot.reschedule}"
    )


async def ensure_daily_digest_reminders(
    session: AsyncSession,
    *,
    now: datetime,
) -> int:
    rows = await session.execute(
        select(UserNotificationPreferences)
        .join(User, User.id == UserNotificationPreferences.user_id)
        .where(
            User.is_active.is_(True),
            or_(
                UserNotificationPreferences.morning_digest_enabled.is_(True),
                UserNotificationPreferences.evening_digest_enabled.is_(True),
            ),
        )
    )
    created = 0
    for (preferences,) in rows:
        timezone = preferences.timezone
        preferences_zone = effective_preferences(
            preferences,
            NotificationDefaults(
                timezone=timezone,
                morning_digest_time=preferences.morning_digest_time,
                evening_digest_time=preferences.evening_digest_time,
                quiet_hours_start=preferences.quiet_hours_start,
                quiet_hours_end=preferences.quiet_hours_end,
                snooze_minutes=preferences.default_snooze_minutes,
            ),
        )
        local_date = now.astimezone(preferences_zone.zoneinfo).date()
        definitions = (
            (
                ReminderType.MORNING_DIGEST,
                preferences.morning_digest_enabled,
                preferences.morning_digest_time,
            ),
            (
                ReminderType.EVENING_DIGEST,
                preferences.evening_digest_enabled,
                preferences.evening_digest_time,
            ),
        )
        for reminder_type, enabled, digest_time in definitions:
            if not enabled:
                continue
            scheduled_at = resolve_local_datetime(
                local_date, digest_time, preferences_zone.zoneinfo
            ).astimezone(UTC)
            statement = (
                insert(Reminder)
                .values(
                    id=uuid4(),
                    user_id=preferences.user_id,
                    type=reminder_type.value,
                    scheduled_at=scheduled_at,
                    schedule_kind="manual",
                    status=ReminderStatus.PENDING.value,
                    deduplication_key=f"digest:{reminder_type.value}:{local_date}",
                    digest_local_date=local_date,
                    schedule_timezone=timezone,
                )
                .on_conflict_do_update(
                    constraint="uq_reminders_user_digest_local_date",
                    set_={
                        "scheduled_at": scheduled_at,
                        "schedule_timezone": timezone,
                        "status": case(
                            (Reminder.sent_at.is_not(None), Reminder.status),
                            else_=ReminderStatus.PENDING.value,
                        ),
                        "cancelled_at": case(
                            (Reminder.sent_at.is_not(None), Reminder.cancelled_at),
                            else_=None,
                        ),
                    },
                )
                .returning(Reminder.id)
            )
            if (await session.scalar(statement)) is not None:
                created += 1
    await session.flush()
    return created


async def prepare_digest_message(
    session: AsyncSession,
    user_id: UUID,
    reminder_type: ReminderType,
    *,
    now: datetime,
    defaults: NotificationDefaults,
) -> str | None:
    from flowmate.reminders.preferences import (
        get_effective_notification_preferences,
    )

    preferences = await get_effective_notification_preferences(
        session, user_id, defaults
    )
    enabled = (
        preferences.morning_digest_enabled
        if reminder_type is ReminderType.MORNING_DIGEST
        else preferences.evening_digest_enabled
    )
    if not enabled:
        return None
    snapshot = await build_digest_snapshot(
        session,
        user_id,
        reminder_type,
        now=now,
        preferences=preferences,
    )
    if snapshot.empty and not preferences.send_empty_digests:
        return None
    return format_digest_message(reminder_type, snapshot)


async def cancel_future_digests(
    session: AsyncSession,
    user_id: UUID,
    reminder_type: ReminderType,
    *,
    now: datetime,
) -> None:
    values = list(
        await session.scalars(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.type == reminder_type.value,
                Reminder.status.in_(
                    [ReminderStatus.PENDING.value, ReminderStatus.SNOOZED.value]
                ),
                Reminder.sent_at.is_(None),
            )
            .with_for_update()
        )
    )
    for value in values:
        value.status = ReminderStatus.CANCELLED.value
        value.cancelled_at = now
        value.snoozed_until = None


async def list_digest_reschedule_items(
    session: AsyncSession,
    user_id: UUID,
    *,
    local_date: date,
    preferences: EffectiveNotificationPreferences,
) -> list[WorkItem]:
    timezone = preferences.zoneinfo
    midnight = resolve_local_datetime(local_date, datetime.min.time(), timezone)
    end = resolve_local_datetime(
        local_date + timedelta(days=1), datetime.min.time(), timezone
    )
    return list(
        await session.scalars(
            select(WorkItem)
            .where(
                WorkItem.user_id == user_id,
                WorkItem.status.in_(OPEN_STATUSES),
                or_(
                    and_(
                        WorkItem.type.in_(
                            [
                                WorkItemType.TASK.value,
                                WorkItemType.QUESTION.value,
                                WorkItemType.DECISION.value,
                                WorkItemType.AGENDA_ITEM.value,
                            ]
                        ),
                        WorkItem.due_at >= midnight,
                        WorkItem.due_at < end,
                    ),
                    and_(
                        WorkItem.type == WorkItemType.FOLLOW_UP.value,
                        WorkItem.next_follow_up_at < end,
                    ),
                ),
            )
            .order_by(WorkItem.created_at)
            .with_for_update()
        )
    )


async def move_digest_items_to_tomorrow(
    session: AsyncSession,
    user_id: UUID,
    *,
    local_date: date,
    telegram_update_id: int,
    preferences: EffectiveNotificationPreferences,
    reminder_policy: ReminderPolicy | None = None,
) -> int:
    from flowmate.task_engine.management import event_for_update

    if await event_for_update(session, user_id, telegram_update_id) is not None:
        return 0
    items = await list_digest_reschedule_items(
        session,
        user_id,
        local_date=local_date,
        preferences=preferences,
    )
    timezone = preferences.zoneinfo
    for index, item in enumerate(items):
        field = (
            "next_follow_up_at"
            if item.type == WorkItemType.FOLLOW_UP.value
            else "due_at"
        )
        previous = getattr(item, field)
        if previous is None:
            continue
        localized = previous.astimezone(timezone)
        new_value = resolve_local_datetime(
            localized.date() + timedelta(days=1),
            localized.time().replace(tzinfo=None),
            timezone,
        )
        setattr(item, field, new_value)
        session.add(
            WorkItemEvent(
                user_id=user_id,
                work_item_id=item.id,
                event_type="rescheduled",
                telegram_update_id=telegram_update_id if index == 0 else None,
                payload={
                    "field": field,
                    "previous": previous.isoformat(),
                    "new": new_value.isoformat(),
                    "source": "evening_digest",
                },
            )
        )
        await sync_work_item_reminders(
            session,
            item,
            policy=reminder_policy,
            allow_final_replacement=True,
        )
    await session.flush()
    return len(items)
