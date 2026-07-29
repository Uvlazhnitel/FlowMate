# ruff: noqa: RUF001
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.errors import AIError
from flowmate.db.models import WorkItem
from flowmate.reminders.parsing import SnoozeParsingError, SnoozeParsingService
from flowmate.reminders.preferences import EffectiveNotificationPreferences
from flowmate.reminders.sync import ReminderPolicy
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.enums import WorkItemType
from flowmate.task_engine.management import (
    MutationResult,
    StaleWorkItemError,
    existing_mutation,
    reschedule_work_item,
    work_item_revision,
)
from flowmate.task_engine.service import get_work_item

UNKNOWN_PHRASE_MESSAGE = (
    "Не удалось понять срок. Напишите, например: завтра утром, через час "
    "или 15 августа в 14:00."
)
LATER_TODAY_UNAVAILABLE_MESSAGE = (
    "Сегодня уже недостаточно времени. Выберите завтра или другую дату."
)


class ReschedulePreset(StrEnum):
    LATER_TODAY = "later_today"
    TOMORROW_MORNING = "tomorrow_morning"
    NEXT_WORKING_DAY = "next_working_day"
    NEXT_WEEK = "next_week"


class ReschedulingError(ValueError):
    """A rescheduling request could not be resolved safely."""


class UnknownReschedulePhraseError(ReschedulingError):
    def __init__(self) -> None:
        super().__init__(UNKNOWN_PHRASE_MESSAGE)


class LaterTodayUnavailableError(ReschedulingError):
    def __init__(self) -> None:
        super().__init__(LATER_TODAY_UNAVAILABLE_MESSAGE)


def effective_schedule(item: WorkItem) -> datetime | None:
    if item.type == WorkItemType.FOLLOW_UP.value:
        return item.next_follow_up_at or item.due_at
    return item.due_at


def _next_weekday(value: datetime) -> datetime:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


class ReschedulingService:
    def __init__(self, parser: SnoozeParsingService) -> None:
        self._parser = parser

    def resolve_preset(
        self,
        item: WorkItem,
        preset: ReschedulePreset,
        *,
        preferences: EffectiveNotificationPreferences,
        now: datetime,
    ) -> datetime:
        timezone = preferences.zoneinfo
        local_now = now.astimezone(timezone)
        current = effective_schedule(item)
        current_time = (
            current.astimezone(timezone).time().replace(tzinfo=None)
            if current is not None
            else preferences.default_reminder_time
        )

        if preset is ReschedulePreset.LATER_TODAY:
            candidate = local_now + timedelta(hours=3)
            candidate_minutes = candidate.hour * 60 + candidate.minute
            if candidate.second or candidate.microsecond:
                candidate_minutes += 1
            rounded_minutes = ((candidate_minutes + 14) // 15) * 15
            rounded_hour, minute = divmod(rounded_minutes, 60)
            target_date = candidate.date()
            if rounded_hour == 24:
                target_date += timedelta(days=1)
                rounded_hour = 0
            if target_date != local_now.date():
                raise LaterTodayUnavailableError
            target_time = time(rounded_hour, minute)
        elif preset is ReschedulePreset.TOMORROW_MORNING:
            target_date = local_now.date() + timedelta(days=1)
            target_time = preferences.default_reminder_time
        elif preset is ReschedulePreset.NEXT_WORKING_DAY:
            next_day = _next_weekday(local_now)
            target_date = next_day.date()
            target_time = current_time
        else:
            target_date = local_now.date() + timedelta(days=7)
            target_time = current_time

        return resolve_local_datetime(target_date, target_time, timezone).astimezone(
            UTC
        )

    async def resolve_text(
        self,
        item: WorkItem,
        phrase: str,
        *,
        preferences: EffectiveNotificationPreferences,
        now: datetime,
    ) -> datetime:
        current = effective_schedule(item)
        default_time = (
            current.astimezone(preferences.zoneinfo).time().replace(tzinfo=None)
            if current is not None
            else preferences.default_reminder_time
        )
        try:
            target = await self._parser.parse(
                phrase,
                timezone=preferences.zoneinfo,
                now=now,
                default_time=default_time,
            )
        except (AIError, SnoozeParsingError) as error:
            raise UnknownReschedulePhraseError from error
        return target.astimezone(UTC)

    async def reschedule_preset(
        self,
        session: AsyncSession,
        user_id: UUID,
        work_item_id: UUID,
        telegram_update_id: int | None,
        preset: ReschedulePreset,
        *,
        preferences: EffectiveNotificationPreferences,
        reminder_policy: ReminderPolicy | None = None,
        expected_revision: int | None = None,
        now: datetime | None = None,
    ) -> MutationResult:
        duplicate = await existing_mutation(session, user_id, telegram_update_id)
        if duplicate is not None:
            return duplicate
        item = await self._load_current(
            session,
            user_id,
            work_item_id,
            expected_revision=expected_revision,
        )
        current = now or datetime.now(UTC)
        target = self.resolve_preset(
            item,
            preset,
            preferences=preferences,
            now=current,
        )
        return await reschedule_work_item(
            session,
            user_id,
            work_item_id,
            telegram_update_id,
            target,
            reminder_policy=reminder_policy,
            expected_revision=expected_revision,
        )

    async def reschedule_text(
        self,
        session: AsyncSession,
        user_id: UUID,
        work_item_id: UUID,
        telegram_update_id: int | None,
        phrase: str,
        *,
        preferences: EffectiveNotificationPreferences,
        reminder_policy: ReminderPolicy | None = None,
        expected_revision: int | None = None,
        now: datetime | None = None,
    ) -> MutationResult:
        duplicate = await existing_mutation(session, user_id, telegram_update_id)
        if duplicate is not None:
            return duplicate
        item = await self._load_current(
            session,
            user_id,
            work_item_id,
            expected_revision=expected_revision,
        )
        current = now or datetime.now(UTC)
        target = await self.resolve_text(
            item,
            phrase,
            preferences=preferences,
            now=current,
        )
        return await reschedule_work_item(
            session,
            user_id,
            work_item_id,
            telegram_update_id,
            target,
            reminder_policy=reminder_policy,
            expected_revision=expected_revision,
        )

    @staticmethod
    async def _load_current(
        session: AsyncSession,
        user_id: UUID,
        work_item_id: UUID,
        *,
        expected_revision: int | None,
    ) -> WorkItem:
        item = await get_work_item(session, user_id, work_item_id)
        if item is None:
            raise ValueError("work item not found")
        if (
            expected_revision is not None
            and work_item_revision(item.updated_at) != expected_revision
        ):
            raise StaleWorkItemError("work item card is stale")
        return item
