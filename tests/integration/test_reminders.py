from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.users import create_telegram_user
from flowmate.reminders.enums import ReminderStatus
from flowmate.reminders.service import (
    ClaimedReminder,
    InvalidReminderTransitionError,
    cancel_reminder,
    claim_due_reminders,
    create_reminder,
    get_claimed_reminder_delivery,
    list_pending_reminders,
    mark_reminder_delivery_failure,
    mark_reminder_sent,
)
from flowmate.task_engine.service import create_work_item


@pytest.mark.integration
async def test_reminder_creation_deduplication_and_user_isolation(
    database_session: AsyncSession,
) -> None:
    owner = await create_telegram_user(database_session, 640_001)
    other = await create_telegram_user(database_session, 640_002)
    item = await create_work_item(
        database_session,
        owner.id,
        item_type="task",
        title="Prepare release",
    )
    scheduled_at = datetime(2026, 7, 23, 9, tzinfo=UTC)

    reminder, created = await create_reminder(
        database_session,
        owner.id,
        reminder_type="deadline",
        scheduled_at=scheduled_at,
        deduplication_key="work-item:deadline",
        work_item_id=item.id,
    )
    duplicate, duplicate_created = await create_reminder(
        database_session,
        owner.id,
        reminder_type="custom",
        scheduled_at=scheduled_at + timedelta(days=1),
        deduplication_key="work-item:deadline",
        message="Different request",
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.id == reminder.id
    assert reminder.scheduled_at == scheduled_at
    assert [
        value.id for value in await list_pending_reminders(database_session, owner.id)
    ] == [reminder.id]
    assert await list_pending_reminders(database_session, other.id) == []
    with pytest.raises(ValueError, match="work item not found"):
        await create_reminder(
            database_session,
            other.id,
            reminder_type="deadline",
            scheduled_at=scheduled_at,
            deduplication_key="foreign-item",
            work_item_id=item.id,
        )


@pytest.mark.integration
async def test_claim_selects_only_due_active_reminders_and_sent_is_final(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 640_003)
    now = datetime(2026, 7, 23, 10, tzinfo=UTC)
    due, _ = await create_reminder(
        database_session,
        user.id,
        reminder_type="morning_digest",
        scheduled_at=now,
        deduplication_key="due",
    )
    await create_reminder(
        database_session,
        user.id,
        reminder_type="evening_digest",
        scheduled_at=now + timedelta(hours=2),
        deduplication_key="future",
    )
    cancelled, _ = await create_reminder(
        database_session,
        user.id,
        reminder_type="custom",
        scheduled_at=now - timedelta(minutes=1),
        deduplication_key="cancelled",
        message="Do not send",
    )
    await cancel_reminder(database_session, user.id, cancelled.id, now=now)

    claims = await claim_due_reminders(
        database_session,
        now=now,
        limit=10,
        max_attempts=3,
        processing_timeout=timedelta(minutes=5),
    )

    assert [claim.id for claim in claims] == [due.id]
    delivery = await get_claimed_reminder_delivery(database_session, claims[0])
    assert delivery is not None
    assert delivery.telegram_user_id == 640_003
    assert await mark_reminder_sent(database_session, claims[0], now=now)
    assert due.status == ReminderStatus.SENT.value
    assert due.sent_at == now
    assert (
        await claim_due_reminders(
            database_session,
            now=now + timedelta(hours=3),
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
    )[0].id != due.id
    with pytest.raises(InvalidReminderTransitionError):
        await cancel_reminder(database_session, user.id, due.id, now=now)


@pytest.mark.integration
async def test_claim_token_blocks_duplicate_completion_and_retries(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 640_004)
    now = datetime(2026, 7, 23, 10, tzinfo=UTC)
    reminder, _ = await create_reminder(
        database_session,
        user.id,
        reminder_type="custom",
        scheduled_at=now,
        deduplication_key="retry",
        message="Retry me",
    )
    first_claim = (
        await claim_due_reminders(
            database_session,
            now=now,
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
    )[0]
    assert (
        await claim_due_reminders(
            database_session,
            now=now,
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
        == []
    )
    assert await mark_reminder_delivery_failure(
        database_session,
        first_claim,
        now=now,
        error_code="telegram_network",
        permanent=False,
        max_attempts=3,
        retry_delay=timedelta(minutes=1),
    )
    assert reminder.status == ReminderStatus.PENDING.value
    assert reminder.next_attempt_at == now + timedelta(minutes=1)
    assert (
        await claim_due_reminders(
            database_session,
            now=now + timedelta(seconds=30),
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
        == []
    )
    second_claim = (
        await claim_due_reminders(
            database_session,
            now=now + timedelta(minutes=1),
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
    )[0]
    assert second_claim.processing_token != first_claim.processing_token
    assert not await mark_reminder_sent(
        database_session,
        ClaimedReminder(reminder.id, uuid4()),
        now=now + timedelta(minutes=1),
    )
    assert await mark_reminder_sent(
        database_session,
        second_claim,
        now=now + timedelta(minutes=1),
    )


@pytest.mark.integration
async def test_stale_claim_recovery_and_permanent_failure(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 640_005)
    now = datetime(2026, 7, 23, 10, tzinfo=UTC)
    reminder, _ = await create_reminder(
        database_session,
        user.id,
        reminder_type="custom",
        scheduled_at=now,
        deduplication_key="stale",
        message="Recover me",
    )
    first_claim = (
        await claim_due_reminders(
            database_session,
            now=now,
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
    )[0]
    recovered = (
        await claim_due_reminders(
            database_session,
            now=now + timedelta(minutes=6),
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
    )[0]

    assert recovered.id == first_claim.id
    assert recovered.processing_token != first_claim.processing_token
    assert reminder.delivery_attempts == 2
    assert await mark_reminder_delivery_failure(
        database_session,
        recovered,
        now=now + timedelta(minutes=6),
        error_code="telegram_forbidden",
        permanent=True,
        max_attempts=3,
        retry_delay=timedelta(minutes=1),
    )
    assert reminder.status == ReminderStatus.FAILED.value
    assert reminder.last_error == "telegram_forbidden"
    assert (
        await claim_due_reminders(
            database_session,
            now=now + timedelta(days=1),
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
        == []
    )


@pytest.mark.integration
async def test_temporary_failure_stops_after_maximum_attempts(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 640_006)
    now = datetime(2026, 7, 23, 10, tzinfo=UTC)
    reminder, _ = await create_reminder(
        database_session,
        user.id,
        reminder_type="custom",
        scheduled_at=now,
        deduplication_key="maximum-attempts",
        message="Try once",
    )
    claim = (
        await claim_due_reminders(
            database_session,
            now=now,
            limit=10,
            max_attempts=1,
            processing_timeout=timedelta(minutes=5),
        )
    )[0]

    assert await mark_reminder_delivery_failure(
        database_session,
        claim,
        now=now,
        error_code="telegram_network",
        permanent=False,
        max_attempts=1,
        retry_delay=timedelta(minutes=1),
    )
    assert reminder.status == ReminderStatus.FAILED.value
    assert reminder.delivery_attempts == 1
    assert reminder.next_attempt_at is None
