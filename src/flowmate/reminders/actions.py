from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import Reminder, WorkItem
from flowmate.reminders.enums import ReminderStatus, ReminderType
from flowmate.reminders.service import validate_aware_datetime
from flowmate.task_engine.enums import WorkItemEventType
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    append_management_event,
    event_for_update,
)
from flowmate.task_engine.queries import OPEN_STATUSES


class StaleReminderError(InvalidWorkItemTransitionError):
    """The reminder changed after a Telegram action menu was rendered."""


@dataclass(frozen=True, slots=True)
class ReminderActionTarget:
    reminder: Reminder
    work_item: WorkItem


def action_now() -> datetime:
    return datetime.now(UTC)


def effective_reminder_time(reminder: Reminder) -> datetime:
    return reminder.snoozed_until or reminder.next_attempt_at or reminder.scheduled_at


def reminder_revision(reminder: Reminder) -> int:
    normalized = effective_reminder_time(reminder).astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = normalized - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


async def get_reminder_action_target(
    session: AsyncSession,
    user_id: UUID,
    reminder_id: UUID,
    *,
    for_update: bool = False,
) -> ReminderActionTarget | None:
    statement = (
        select(Reminder, WorkItem)
        .join(WorkItem, WorkItem.id == Reminder.work_item_id)
        .where(
            Reminder.id == reminder_id,
            Reminder.user_id == user_id,
            WorkItem.user_id == user_id,
        )
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        return None
    reminder, work_item = row
    return ReminderActionTarget(reminder, work_item)


def work_item_reference(item: WorkItem, reminder_type: ReminderType) -> datetime:
    reference = (
        item.next_follow_up_at
        if reminder_type is ReminderType.FOLLOW_UP
        else item.due_at
    )
    if reference is None:
        raise InvalidWorkItemTransitionError("work item no longer has a reminder date")
    return reference


async def snooze_work_item_reminder(
    session: AsyncSession,
    user_id: UUID,
    reminder_id: UUID,
    telegram_update_id: int,
    *,
    duration: timedelta | None = None,
    until: datetime | None = None,
    now: datetime | None = None,
    expected_revision: int | None = None,
) -> tuple[Reminder, bool]:
    current = now or action_now()
    validate_aware_datetime(current, "now")
    if (duration is None) == (until is None):
        raise ValueError("provide exactly one snooze duration or target time")
    if until is not None:
        scheduled_at = until
    else:
        if duration is None or duration <= timedelta(0):
            raise ValueError("snooze duration must be positive")
        scheduled_at = current + duration
    validate_aware_datetime(scheduled_at, "snoozed_until")
    if scheduled_at <= current:
        raise ValueError("snooze target must be in the future")
    duplicate = await event_for_update(session, user_id, telegram_update_id)
    if duplicate is not None:
        snoozed_id = duplicate.payload.get("reminder_id")
        if snoozed_id is None:
            raise InvalidWorkItemTransitionError("update was used by another action")
        reminder = await session.scalar(
            select(Reminder).where(
                Reminder.id == UUID(str(snoozed_id)),
                Reminder.user_id == user_id,
            )
        )
        if reminder is None:
            raise ValueError("snoozed reminder not found")
        return reminder, False
    target = await get_reminder_action_target(
        session,
        user_id,
        reminder_id,
        for_update=True,
    )
    if target is None:
        raise ValueError("reminder not found")
    if (
        expected_revision is not None
        and reminder_revision(target.reminder) != expected_revision
    ):
        raise StaleReminderError("reminder action is stale")
    if target.work_item.status not in OPEN_STATUSES:
        raise InvalidWorkItemTransitionError("work item is not active")
    reminder_type = ReminderType(target.reminder.type)
    if reminder_type not in {
        ReminderType.DEADLINE,
        ReminderType.FOLLOW_UP,
        ReminderType.WAITING,
    }:
        raise InvalidWorkItemTransitionError("reminder cannot be snoozed")
    work_item_reference(target.work_item, reminder_type)
    if target.reminder.status in {
        ReminderStatus.CANCELLED.value,
        ReminderStatus.FAILED.value,
    }:
        raise InvalidWorkItemTransitionError("reminder cannot be snoozed")
    previous = effective_reminder_time(target.reminder)
    target.reminder.status = ReminderStatus.SNOOZED.value
    target.reminder.snoozed_until = scheduled_at
    target.reminder.next_attempt_at = None
    target.reminder.processing_started_at = None
    target.reminder.processing_token = None
    target.reminder.cancelled_at = None
    target.reminder.last_error = None
    target.reminder.delivery_attempts = 0
    await append_management_event(
        session,
        target.work_item,
        WorkItemEventType.REMINDER_SNOOZED,
        telegram_update_id,
        {
            "reminder_id": str(target.reminder.id),
            "previous": previous.isoformat(),
            "new": scheduled_at.isoformat(),
        },
    )
    await session.flush()
    return target.reminder, True
