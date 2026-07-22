from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import Reminder, WorkItem
from flowmate.reminders.enums import (
    ReminderScheduleKind,
    ReminderStatus,
    ReminderType,
)
from flowmate.reminders.service import create_reminder, validate_aware_datetime
from flowmate.task_engine.enums import WorkItemStatus, WorkItemType

OPEN_STATUSES = {
    WorkItemStatus.INBOX.value,
    WorkItemStatus.PLANNED.value,
    WorkItemStatus.ACTIVE.value,
    WorkItemStatus.WAITING.value,
    WorkItemStatus.SNOOZED.value,
}
ACTIVE_REMINDER_STATUSES = {
    ReminderStatus.PENDING.value,
    ReminderStatus.PROCESSING.value,
    ReminderStatus.SNOOZED.value,
}
MANAGED_SCHEDULE_KINDS = {
    ReminderScheduleKind.EXACT.value,
    ReminderScheduleKind.BEFORE_DEADLINE.value,
    ReminderScheduleKind.SNOOZE.value,
}


@dataclass(frozen=True, slots=True)
class ReminderPolicy:
    deadline_lead_minutes: int = 0

    def __post_init__(self) -> None:
        if self.deadline_lead_minutes < 0:
            raise ValueError("deadline lead minutes must not be negative")


@dataclass(frozen=True, slots=True)
class ReminderSchedule:
    reminder_type: ReminderType
    schedule_kind: ReminderScheduleKind
    scheduled_at: datetime
    reference_at: datetime


def reminder_now() -> datetime:
    return datetime.now(UTC)


def epoch_microseconds(value: datetime) -> int:
    validate_aware_datetime(value, "reference_at")
    normalized = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = normalized - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def reminder_key_base(work_item_id: object, schedule: ReminderSchedule) -> str:
    return (
        f"work-item:{work_item_id}:{schedule.reminder_type.value}:"
        f"{schedule.schedule_kind.value}:{epoch_microseconds(schedule.reference_at)}"
    )


def schedules_for_work_item(
    item: WorkItem,
    policy: ReminderPolicy,
    *,
    now: datetime,
) -> tuple[ReminderSchedule, ...]:
    validate_aware_datetime(now, "now")
    if item.status not in OPEN_STATUSES:
        return ()
    if item.type == WorkItemType.FOLLOW_UP.value:
        if item.next_follow_up_at is None:
            return ()
        return (
            ReminderSchedule(
                ReminderType.FOLLOW_UP,
                ReminderScheduleKind.EXACT,
                item.next_follow_up_at,
                item.next_follow_up_at,
            ),
        )
    if item.type == WorkItemType.WAITING.value:
        if item.due_at is None:
            return ()
        return (
            ReminderSchedule(
                ReminderType.WAITING,
                ReminderScheduleKind.EXACT,
                item.due_at,
                item.due_at,
            ),
        )
    if item.due_at is None:
        return ()
    schedules = [
        ReminderSchedule(
            ReminderType.DEADLINE,
            ReminderScheduleKind.EXACT,
            item.due_at,
            item.due_at,
        )
    ]
    if policy.deadline_lead_minutes:
        before = item.due_at - timedelta(minutes=policy.deadline_lead_minutes)
        if before > now:
            schedules.append(
                ReminderSchedule(
                    ReminderType.DEADLINE,
                    ReminderScheduleKind.BEFORE_DEADLINE,
                    before,
                    item.due_at,
                )
            )
    return tuple(schedules)


def _matches_base(key: str, base: str) -> bool:
    return key == base or key.startswith(f"{base}:v")


def _next_versioned_key(base: str, reminders: list[Reminder]) -> str:
    keys = {reminder.deduplication_key for reminder in reminders}
    if base not in keys:
        return base
    version = 2
    while f"{base}:v{version}" in keys:
        version += 1
    return f"{base}:v{version}"


def cancel_reminder_record(reminder: Reminder, *, now: datetime) -> None:
    reminder.status = ReminderStatus.CANCELLED.value
    reminder.cancelled_at = now
    reminder.next_attempt_at = None
    reminder.snoozed_until = None
    reminder.processing_started_at = None
    reminder.processing_token = None


async def cancel_work_item_reminders(
    session: AsyncSession,
    work_item: WorkItem,
    *,
    now: datetime | None = None,
) -> int:
    current = now or reminder_now()
    validate_aware_datetime(current, "now")
    reminders = list(
        await session.scalars(
            select(Reminder)
            .where(
                Reminder.user_id == work_item.user_id,
                Reminder.work_item_id == work_item.id,
                Reminder.status.in_(ACTIVE_REMINDER_STATUSES),
            )
            .with_for_update()
        )
    )
    for reminder in reminders:
        cancel_reminder_record(reminder, now=current)
    if reminders:
        await session.flush()
    return len(reminders)


async def sync_work_item_reminders(
    session: AsyncSession,
    work_item: WorkItem,
    *,
    policy: ReminderPolicy | None = None,
    now: datetime | None = None,
    allow_final_replacement: bool = False,
) -> list[Reminder]:
    current = now or reminder_now()
    validate_aware_datetime(current, "now")
    selected_policy = policy or ReminderPolicy()
    schedules = schedules_for_work_item(work_item, selected_policy, now=current)
    existing = list(
        await session.scalars(
            select(Reminder)
            .where(
                Reminder.user_id == work_item.user_id,
                Reminder.work_item_id == work_item.id,
                Reminder.schedule_kind.in_(MANAGED_SCHEDULE_KINDS),
            )
            .with_for_update()
        )
    )
    bases = {
        reminder_key_base(work_item.id, schedule): schedule for schedule in schedules
    }
    changed = False
    for reminder in existing:
        if reminder.status not in ACTIVE_REMINDER_STATUSES:
            continue
        if not any(_matches_base(reminder.deduplication_key, base) for base in bases):
            cancel_reminder_record(reminder, now=current)
            changed = True
    if changed:
        await session.flush()

    synchronized: list[Reminder] = []
    for base, schedule in bases.items():
        candidates = [
            reminder
            for reminder in existing
            if _matches_base(reminder.deduplication_key, base)
        ]
        active = next(
            (
                reminder
                for reminder in candidates
                if reminder.status in ACTIVE_REMINDER_STATUSES
            ),
            None,
        )
        if active is not None:
            synchronized.append(active)
            continue
        if candidates and not allow_final_replacement:
            continue
        key = _next_versioned_key(base, candidates)
        reminder, _ = await create_reminder(
            session,
            work_item.user_id,
            reminder_type=schedule.reminder_type,
            scheduled_at=schedule.scheduled_at,
            deduplication_key=key,
            work_item_id=work_item.id,
            reference_at=schedule.reference_at,
            schedule_kind=schedule.schedule_kind,
        )
        existing.append(reminder)
        synchronized.append(reminder)
    return synchronized
