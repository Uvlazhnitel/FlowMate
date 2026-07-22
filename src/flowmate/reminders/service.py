import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import Person, Reminder, Topic, User, WorkItem, WorkItemPerson
from flowmate.reminders.enums import (
    ReminderScheduleKind,
    ReminderStatus,
    ReminderType,
)
from flowmate.task_engine.enums import WorkItemStatus

if TYPE_CHECKING:
    from flowmate.reminders.preferences import NotificationDefaults

ERROR_CODE_PATTERN = re.compile(r"[a-z0-9_]{1,64}")


class InvalidReminderTransitionError(ValueError):
    """The requested reminder state transition is not allowed."""


@dataclass(frozen=True, slots=True)
class ClaimedReminder:
    id: UUID
    processing_token: UUID


@dataclass(frozen=True, slots=True)
class ReminderDelivery:
    reminder_id: UUID
    processing_token: UUID
    reminder_type: ReminderType
    schedule_kind: ReminderScheduleKind
    telegram_user_id: int | None
    work_item_id: UUID | None
    work_item_title: str | None
    topic_name: str | None
    person_names: tuple[str, ...]
    scheduled_at: datetime
    reference_at: datetime | None
    message: str | None
    user_id: UUID | None = None
    timezone: str = "UTC"


def validate_aware_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def normalize_required_text(value: str, field_name: str, max_length: int) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    if len(normalized) > max_length:
        raise ValueError(f"{field_name} is too long")
    return normalized


def normalize_optional_message(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError("message must not be blank")
    return normalized


async def create_reminder(
    session: AsyncSession,
    user_id: UUID,
    *,
    reminder_type: ReminderType | str,
    scheduled_at: datetime,
    deduplication_key: str,
    work_item_id: UUID | None = None,
    message: str | None = None,
    reference_at: datetime | None = None,
    schedule_kind: ReminderScheduleKind | str = ReminderScheduleKind.MANUAL,
) -> tuple[Reminder, bool]:
    parsed_type = ReminderType(reminder_type)
    parsed_schedule_kind = ReminderScheduleKind(schedule_kind)
    validate_aware_datetime(scheduled_at, "scheduled_at")
    if reference_at is not None:
        validate_aware_datetime(reference_at, "reference_at")
    normalized_key = normalize_required_text(
        deduplication_key,
        "deduplication_key",
        255,
    )
    normalized_message = normalize_optional_message(message)
    existing = (
        await session.scalars(
            select(Reminder).where(
                Reminder.user_id == user_id,
                Reminder.deduplication_key == normalized_key,
            )
        )
    ).one_or_none()
    if existing is not None:
        return existing, False
    if work_item_id is not None:
        owned_item = await session.scalar(
            select(WorkItem.id).where(
                WorkItem.id == work_item_id,
                WorkItem.user_id == user_id,
            )
        )
        if owned_item is None:
            raise ValueError("work item not found")
    if parsed_schedule_kind is not ReminderScheduleKind.MANUAL and (
        work_item_id is None or reference_at is None
    ):
        raise ValueError("managed reminder requires a work item and reference date")
    if (
        parsed_type is ReminderType.CUSTOM
        and work_item_id is None
        and normalized_message is None
    ):
        raise ValueError("custom reminder requires a work item or message")
    try:
        async with session.begin_nested():
            reminder = Reminder(
                user_id=user_id,
                work_item_id=work_item_id,
                type=parsed_type.value,
                scheduled_at=scheduled_at,
                reference_at=reference_at,
                schedule_kind=parsed_schedule_kind.value,
                message=normalized_message,
                deduplication_key=normalized_key,
            )
            session.add(reminder)
            await session.flush()
        return reminder, True
    except IntegrityError:
        duplicate = (
            await session.scalars(
                select(Reminder).where(
                    Reminder.user_id == user_id,
                    Reminder.deduplication_key == normalized_key,
                )
            )
        ).one_or_none()
        if duplicate is None:
            raise
        return duplicate, False


async def cancel_reminder(
    session: AsyncSession,
    user_id: UUID,
    reminder_id: UUID,
    *,
    now: datetime,
) -> Reminder:
    validate_aware_datetime(now, "now")
    reminder = await session.scalar(
        select(Reminder)
        .where(Reminder.id == reminder_id, Reminder.user_id == user_id)
        .with_for_update()
    )
    if reminder is None:
        raise ValueError("reminder not found")
    if reminder.status == ReminderStatus.CANCELLED.value:
        return reminder
    if reminder.status not in {
        ReminderStatus.PENDING.value,
        ReminderStatus.SNOOZED.value,
        ReminderStatus.FAILED.value,
    }:
        raise InvalidReminderTransitionError("reminder cannot be cancelled")
    reminder.status = ReminderStatus.CANCELLED.value
    reminder.cancelled_at = now
    reminder.next_attempt_at = None
    reminder.processing_started_at = None
    reminder.processing_token = None
    await session.flush()
    return reminder


async def list_pending_reminders(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 100,
) -> list[Reminder]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    effective_at = func.coalesce(
        Reminder.next_attempt_at,
        Reminder.snoozed_until,
        Reminder.scheduled_at,
    )
    statement = (
        select(Reminder)
        .where(
            Reminder.user_id == user_id,
            Reminder.status.in_(
                [ReminderStatus.PENDING.value, ReminderStatus.SNOOZED.value]
            ),
        )
        .order_by(effective_at, Reminder.created_at)
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def claim_due_reminders(
    session: AsyncSession,
    *,
    now: datetime,
    limit: int,
    max_attempts: int,
    processing_timeout: timedelta,
) -> list[ClaimedReminder]:
    validate_aware_datetime(now, "now")
    if limit <= 0 or max_attempts <= 0:
        raise ValueError("limit and max_attempts must be positive")
    if processing_timeout <= timedelta(0):
        raise ValueError("processing_timeout must be positive")
    stale_before = now - processing_timeout
    await session.execute(
        update(Reminder)
        .where(
            Reminder.status == ReminderStatus.PROCESSING.value,
            Reminder.processing_started_at <= stale_before,
            Reminder.delivery_attempts >= max_attempts,
        )
        .values(
            status=ReminderStatus.FAILED.value,
            last_error="processing_timeout",
            processing_started_at=None,
            processing_token=None,
        )
    )
    pending_due = and_(
        Reminder.status == ReminderStatus.PENDING.value,
        func.coalesce(Reminder.next_attempt_at, Reminder.scheduled_at) <= now,
    )
    snoozed_due = and_(
        Reminder.status == ReminderStatus.SNOOZED.value,
        Reminder.snoozed_until.is_not(None),
        Reminder.snoozed_until <= now,
    )
    stale_processing = and_(
        Reminder.status == ReminderStatus.PROCESSING.value,
        Reminder.processing_started_at <= stale_before,
    )
    effective_at = case(
        (Reminder.status == ReminderStatus.SNOOZED.value, Reminder.snoozed_until),
        (
            Reminder.status == ReminderStatus.PROCESSING.value,
            Reminder.processing_started_at,
        ),
        else_=func.coalesce(Reminder.next_attempt_at, Reminder.scheduled_at),
    )
    reminders = list(
        await session.scalars(
            select(Reminder)
            .where(
                or_(pending_due, snoozed_due, stale_processing),
                Reminder.delivery_attempts < max_attempts,
            )
            .order_by(effective_at, Reminder.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    claimed: list[ClaimedReminder] = []
    for reminder in reminders:
        token = uuid4()
        reminder.status = ReminderStatus.PROCESSING.value
        reminder.processing_started_at = now
        reminder.processing_token = token
        reminder.delivery_attempts += 1
        reminder.last_error = None
        claimed.append(ClaimedReminder(reminder.id, token))
    await session.flush()
    return claimed


async def get_claimed_reminder_delivery(
    session: AsyncSession,
    claim: ClaimedReminder,
    *,
    now: datetime | None = None,
    notification_defaults: "NotificationDefaults | None" = None,
) -> ReminderDelivery | None:
    from flowmate.reminders.preferences import (
        NotificationDefaults,
        get_effective_notification_preferences,
        quiet_hours_deferred_until,
    )

    current = now or datetime.now(UTC)
    validate_aware_datetime(current, "now")
    row = (
        await session.execute(
            select(
                Reminder,
                User.telegram_user_id,
                WorkItem.id,
                WorkItem.title,
                WorkItem.status,
                WorkItem.type,
                WorkItem.due_at,
                WorkItem.next_follow_up_at,
                Topic.name,
            )
            .join(User, User.id == Reminder.user_id)
            .outerjoin(
                WorkItem,
                and_(
                    WorkItem.id == Reminder.work_item_id,
                    WorkItem.user_id == Reminder.user_id,
                ),
            )
            .outerjoin(
                Topic,
                and_(
                    Topic.id == WorkItem.topic_id,
                    Topic.user_id == Reminder.user_id,
                ),
            )
            .where(
                Reminder.id == claim.id,
                Reminder.status == ReminderStatus.PROCESSING.value,
                Reminder.processing_token == claim.processing_token,
            )
            .with_for_update(of=Reminder)
        )
    ).one_or_none()
    if row is None:
        return None
    (
        reminder,
        telegram_user_id,
        work_item_id,
        work_item_title,
        work_item_status,
        work_item_type,
        due_at,
        next_follow_up_at,
        topic_name,
    ) = row
    schedule_kind = ReminderScheduleKind(reminder.schedule_kind)
    if schedule_kind is not ReminderScheduleKind.MANUAL:
        reference_at = next_follow_up_at if work_item_type == "follow_up" else due_at
        stale = (
            work_item_id is None
            or work_item_status
            not in {
                WorkItemStatus.INBOX.value,
                WorkItemStatus.PLANNED.value,
                WorkItemStatus.ACTIVE.value,
                WorkItemStatus.WAITING.value,
                WorkItemStatus.SNOOZED.value,
            }
            or reference_at is None
            or reminder.reference_at != reference_at
            or (
                schedule_kind is ReminderScheduleKind.BEFORE_DEADLINE
                and reminder.reference_at is not None
                and current >= reminder.reference_at
            )
        )
        if stale:
            reminder.status = ReminderStatus.CANCELLED.value
            reminder.cancelled_at = current
            reminder.processing_started_at = None
            reminder.processing_token = None
            reminder.next_attempt_at = None
            reminder.snoozed_until = None
            await session.flush()
            return None
    person_names: tuple[str, ...] = ()
    if work_item_id is not None:
        person_names = tuple(
            await session.scalars(
                select(Person.display_name)
                .join(WorkItemPerson, WorkItemPerson.person_id == Person.id)
                .where(
                    WorkItemPerson.user_id == reminder.user_id,
                    WorkItemPerson.work_item_id == work_item_id,
                    Person.user_id == reminder.user_id,
                )
                .order_by(Person.display_name)
            )
        )
    defaults = notification_defaults or NotificationDefaults(
        timezone="UTC",
        morning_digest_time=datetime.min.time(),
        evening_digest_time=datetime.min.time(),
        quiet_hours_start=datetime.min.time(),
        quiet_hours_end=datetime.max.time().replace(microsecond=0),
        snooze_minutes=60,
    )
    preferences = await get_effective_notification_preferences(
        session, reminder.user_id, defaults
    )
    if reminder.type not in {
        ReminderType.MORNING_DIGEST.value,
        ReminderType.EVENING_DIGEST.value,
    }:
        deferred_until = quiet_hours_deferred_until(
            reminder.id,
            now=current,
            preferences=preferences,
        )
        if deferred_until is not None:
            reminder.status = ReminderStatus.SNOOZED.value
            reminder.snoozed_until = deferred_until
            reminder.next_attempt_at = None
            reminder.processing_started_at = None
            reminder.processing_token = None
            reminder.delivery_attempts = max(0, reminder.delivery_attempts - 1)
            await session.flush()
            return None
    return ReminderDelivery(
        reminder_id=reminder.id,
        processing_token=claim.processing_token,
        reminder_type=ReminderType(reminder.type),
        schedule_kind=schedule_kind,
        telegram_user_id=telegram_user_id,
        work_item_id=work_item_id,
        work_item_title=work_item_title,
        topic_name=topic_name,
        person_names=person_names,
        scheduled_at=reminder.scheduled_at,
        reference_at=reminder.reference_at,
        message=reminder.message,
        user_id=reminder.user_id,
        timezone=preferences.timezone,
    )


async def cancel_claimed_reminder(
    session: AsyncSession,
    claim: ClaimedReminder,
    *,
    now: datetime,
    reason: str,
) -> bool:
    reminder = await session.scalar(
        select(Reminder)
        .where(
            Reminder.id == claim.id,
            Reminder.status == ReminderStatus.PROCESSING.value,
            Reminder.processing_token == claim.processing_token,
        )
        .with_for_update()
    )
    if reminder is None:
        return False
    reminder.status = ReminderStatus.CANCELLED.value
    reminder.cancelled_at = now
    reminder.last_error = normalize_required_text(reason, "reason", 64)
    reminder.processing_started_at = None
    reminder.processing_token = None
    await session.flush()
    return True


async def mark_reminder_sent(
    session: AsyncSession,
    claim: ClaimedReminder,
    *,
    now: datetime,
) -> bool:
    validate_aware_datetime(now, "now")
    reminder = await session.scalar(
        select(Reminder)
        .where(
            Reminder.id == claim.id,
            Reminder.status == ReminderStatus.PROCESSING.value,
            Reminder.processing_token == claim.processing_token,
        )
        .with_for_update()
    )
    if reminder is None:
        return False
    reminder.status = ReminderStatus.SENT.value
    reminder.sent_at = now
    reminder.last_error = None
    reminder.next_attempt_at = None
    reminder.processing_started_at = None
    reminder.processing_token = None
    await session.flush()
    return True


async def mark_reminder_delivery_failure(
    session: AsyncSession,
    claim: ClaimedReminder,
    *,
    now: datetime,
    error_code: str,
    permanent: bool,
    max_attempts: int,
    retry_delay: timedelta,
) -> bool:
    validate_aware_datetime(now, "now")
    if max_attempts <= 0 or retry_delay <= timedelta(0):
        raise ValueError("max_attempts and retry_delay must be positive")
    if ERROR_CODE_PATTERN.fullmatch(error_code) is None:
        raise ValueError("error_code must be a safe category")
    reminder = await session.scalar(
        select(Reminder)
        .where(
            Reminder.id == claim.id,
            Reminder.status == ReminderStatus.PROCESSING.value,
            Reminder.processing_token == claim.processing_token,
        )
        .with_for_update()
    )
    if reminder is None:
        return False
    reminder.last_error = error_code
    reminder.processing_started_at = None
    reminder.processing_token = None
    if permanent or reminder.delivery_attempts >= max_attempts:
        reminder.status = ReminderStatus.FAILED.value
        reminder.next_attempt_at = None
    else:
        reminder.status = ReminderStatus.PENDING.value
        reminder.next_attempt_at = now + retry_delay
    await session.flush()
    return True
