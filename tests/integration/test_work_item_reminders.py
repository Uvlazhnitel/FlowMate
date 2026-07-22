from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.handlers.reminders import reminder_callback
from flowmate.db.models import Reminder, WorkItemEvent
from flowmate.db.users import create_telegram_user
from flowmate.reminders.actions import snooze_work_item_reminder
from flowmate.reminders.enums import ReminderScheduleKind, ReminderStatus
from flowmate.reminders.service import (
    claim_due_reminders,
    get_claimed_reminder_delivery,
    mark_reminder_sent,
)
from flowmate.reminders.sync import ReminderPolicy
from flowmate.task_engine.management import (
    archive_work_item,
    cancel_work_item,
    complete_work_item,
    create_follow_up_from_waiting,
    mark_follow_up_replied,
    mark_waiting_received,
    reschedule_work_item,
)
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
)


async def reminders_for_item(
    session: AsyncSession,
    item_id: object,
) -> list[Reminder]:
    return list(
        await session.scalars(
            select(Reminder)
            .where(Reminder.work_item_id == item_id)
            .order_by(Reminder.created_at, Reminder.deduplication_key)
        )
    )


@pytest.mark.integration
async def test_work_item_creation_builds_exact_and_optional_lead_reminders(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_001)
    now = datetime(2026, 7, 24, 9, tzinfo=UTC)
    due_at = now + timedelta(hours=4)

    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Prepare release",
        due_at=due_at,
        reminder_policy=ReminderPolicy(deadline_lead_minutes=60),
        reminder_now=now,
    )
    reminders = await reminders_for_item(database_session, item.id)

    assert [(value.schedule_kind, value.scheduled_at) for value in reminders] == [
        (ReminderScheduleKind.BEFORE_DEADLINE.value, due_at - timedelta(hours=1)),
        (ReminderScheduleKind.EXACT.value, due_at),
    ]
    assert all(value.reference_at == due_at for value in reminders)
    assert len({value.deduplication_key for value in reminders}) == 2

    late_item = await create_work_item(
        database_session,
        user.id,
        item_type="question",
        title="Confirm scope",
        due_at=now + timedelta(minutes=30),
        reminder_policy=ReminderPolicy(deadline_lead_minutes=60),
        reminder_now=now,
    )
    late_reminders = await reminders_for_item(database_session, late_item.id)
    assert [value.schedule_kind for value in late_reminders] == [
        ReminderScheduleKind.EXACT.value
    ]


@pytest.mark.integration
async def test_reschedule_cancels_old_and_versions_a_previously_sent_date(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_002)
    first_date = datetime(2026, 7, 24, 10, tzinfo=UTC)
    second_date = first_date + timedelta(days=1)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Publish release",
        due_at=first_date,
        reminder_now=first_date - timedelta(days=1),
    )
    original = (await reminders_for_item(database_session, item.id))[0]
    original.status = ReminderStatus.SENT.value
    original.sent_at = first_date
    await database_session.flush()

    await reschedule_work_item(
        database_session,
        user.id,
        item.id,
        850_001,
        second_date,
    )
    await reschedule_work_item(
        database_session,
        user.id,
        item.id,
        850_002,
        first_date,
    )
    reminders = await reminders_for_item(database_session, item.id)
    first_date_reminders = [
        value for value in reminders if value.reference_at == first_date
    ]
    second = next(value for value in reminders if value.reference_at == second_date)

    assert original.status == ReminderStatus.SENT.value
    assert len(first_date_reminders) == 2
    assert any(value.deduplication_key.endswith(":v2") for value in reminders)
    assert second.status == ReminderStatus.CANCELLED.value
    assert (
        next(value for value in first_date_reminders if value.id != original.id).status
        == ReminderStatus.PENDING.value
    )


@pytest.mark.integration
@pytest.mark.parametrize("operation", ["complete", "cancel", "archive"])
async def test_terminal_transitions_cancel_active_reminders(
    database_session: AsyncSession,
    operation: str,
) -> None:
    user = await create_telegram_user(
        database_session,
        {"complete": 650_003, "cancel": 650_004, "archive": 650_005}[operation],
    )
    due_at = datetime(2026, 7, 25, 10, tzinfo=UTC)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title=f"Transition {operation}",
        due_at=due_at,
    )
    if operation == "complete":
        await complete_work_item(database_session, user.id, item.id, 850_010)
    elif operation == "cancel":
        await cancel_work_item(database_session, user.id, item.id, 850_011)
    else:
        await archive_work_item(database_session, user.id, item.id, 850_012)

    reminders = await reminders_for_item(database_session, item.id)
    assert [value.status for value in reminders] == [ReminderStatus.CANCELLED.value]


@pytest.mark.integration
async def test_delivery_revalidates_date_and_loads_current_people_and_topic(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_006)
    topic = await create_topic(database_session, user.id, "Testing")
    person = await create_person(database_session, user.id, "Anton")
    due_at = datetime(2026, 7, 24, 10, tzinfo=UTC)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="follow_up",
        title="Уточнить сроки",
        topic_id=topic.id,
        next_follow_up_at=due_at,
    )
    await link_person_to_work_item(database_session, user.id, item.id, person.id)
    claim = (
        await claim_due_reminders(
            database_session,
            now=due_at + timedelta(hours=2),
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
    )[0]
    delivery = await get_claimed_reminder_delivery(
        database_session,
        claim,
        now=due_at + timedelta(hours=2),
    )

    assert delivery is not None
    assert delivery.topic_name == "Testing"
    assert delivery.person_names == ("Anton",)
    assert delivery.reference_at == due_at
    assert await mark_reminder_sent(
        database_session,
        claim,
        now=due_at + timedelta(hours=2),
    )

    stale_item = await create_work_item(
        database_session,
        user.id,
        item_type="waiting",
        title="Wait for report",
        status="waiting",
        due_at=due_at,
        waiting_since=due_at - timedelta(days=1),
    )
    stale_claim = (
        await claim_due_reminders(
            database_session,
            now=due_at + timedelta(hours=2),
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
    )[0]
    stale_item.due_at = due_at + timedelta(days=1)
    await database_session.flush()

    assert (
        await get_claimed_reminder_delivery(
            database_session,
            stale_claim,
            now=due_at + timedelta(hours=2),
        )
        is None
    )
    stale_reminder = await database_session.get(Reminder, stale_claim.id)
    assert stale_reminder is not None
    assert stale_reminder.status == ReminderStatus.CANCELLED.value


@pytest.mark.integration
async def test_delivery_rejects_cross_user_work_item_link(
    database_session: AsyncSession,
) -> None:
    owner = await create_telegram_user(database_session, 650_020)
    other = await create_telegram_user(database_session, 650_021)
    now = datetime(2026, 7, 24, 9, tzinfo=UTC)
    other_item = await create_work_item(
        database_session,
        other.id,
        item_type="task",
        title="Private title",
        due_at=now + timedelta(days=1),
    )
    reminder = Reminder(
        user_id=owner.id,
        work_item_id=other_item.id,
        type="deadline",
        scheduled_at=now,
        reference_at=now + timedelta(days=1),
        schedule_kind=ReminderScheduleKind.EXACT.value,
        deduplication_key="cross-user-corrupt-link",
    )
    database_session.add(reminder)
    await database_session.flush()
    claims = await claim_due_reminders(
        database_session,
        now=now,
        limit=1,
        max_attempts=3,
        processing_timeout=timedelta(minutes=5),
    )

    assert len(claims) == 1
    assert (
        await get_claimed_reminder_delivery(
            database_session,
            claims[0],
            now=now,
        )
        is None
    )
    assert reminder.status == ReminderStatus.CANCELLED.value


@pytest.mark.integration
async def test_late_pre_deadline_and_deleted_item_reminders_are_cancelled(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_009)
    now = datetime(2026, 7, 24, 9, tzinfo=UTC)
    due_at = now + timedelta(hours=2)
    await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Deadline",
        due_at=due_at,
        reminder_policy=ReminderPolicy(deadline_lead_minutes=60),
        reminder_now=now,
    )
    claims = await claim_due_reminders(
        database_session,
        now=due_at + timedelta(minutes=1),
        limit=10,
        max_attempts=3,
        processing_timeout=timedelta(minutes=5),
    )
    deliveries = [
        await get_claimed_reminder_delivery(
            database_session,
            claim,
            now=due_at + timedelta(minutes=1),
        )
        for claim in claims
    ]
    assert sum(delivery is not None for delivery in deliveries) == 1
    assert (
        next(delivery for delivery in deliveries if delivery is not None).schedule_kind
        is ReminderScheduleKind.EXACT
    )

    deleted = await create_work_item(
        database_session,
        user.id,
        item_type="waiting",
        title="Deleted wait",
        status="waiting",
        due_at=now,
        waiting_since=now - timedelta(days=1),
    )
    deleted_reminder = (await reminders_for_item(database_session, deleted.id))[0]
    await database_session.delete(deleted)
    await database_session.flush()
    deleted_claim = (
        await claim_due_reminders(
            database_session,
            now=now,
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
    )[0]
    assert deleted_claim.id == deleted_reminder.id
    assert (
        await get_claimed_reminder_delivery(
            database_session,
            deleted_claim,
            now=now,
        )
        is None
    )


@pytest.mark.integration
async def test_snooze_reply_received_and_follow_up_actions_are_idempotent(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_007)
    due_at = datetime(2026, 7, 24, 10, tzinfo=UTC)
    follow_up = await create_work_item(
        database_session,
        user.id,
        item_type="follow_up",
        title="Contact Anton",
        next_follow_up_at=due_at,
    )
    original = (await reminders_for_item(database_session, follow_up.id))[0]
    original.status = ReminderStatus.SENT.value
    original.sent_at = due_at
    snoozed, created = await snooze_work_item_reminder(
        database_session,
        user.id,
        original.id,
        850_020,
        duration=timedelta(hours=1),
        now=due_at,
    )
    duplicate, duplicate_created = await snooze_work_item_reminder(
        database_session,
        user.id,
        original.id,
        850_020,
        duration=timedelta(hours=1),
        now=due_at,
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.id == snoozed.id
    assert snoozed.id == original.id
    assert snoozed.schedule_kind == ReminderScheduleKind.EXACT.value
    assert snoozed.status == ReminderStatus.SNOOZED.value
    assert snoozed.snoozed_until == due_at + timedelta(hours=1)
    assert follow_up.next_follow_up_at == due_at
    reply = await mark_follow_up_replied(
        database_session,
        user.id,
        follow_up.id,
        850_021,
        now=due_at + timedelta(minutes=5),
    )
    assert reply.work_item.status == "done"
    assert reply.work_item.completed_at == due_at + timedelta(minutes=5)
    assert snoozed.status == ReminderStatus.CANCELLED.value

    waiting = await create_work_item(
        database_session,
        user.id,
        item_type="waiting",
        title="Wait for answer",
        status="waiting",
        due_at=due_at,
        waiting_since=due_at - timedelta(days=1),
    )
    created_follow_up, created = await create_follow_up_from_waiting(
        database_session,
        user.id,
        waiting.id,
        850_022,
        require_received=False,
    )
    duplicate_follow_up, duplicate_created = await create_follow_up_from_waiting(
        database_session,
        user.id,
        waiting.id,
        850_022,
        require_received=False,
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate_follow_up.id == created_follow_up.id

    received = await mark_waiting_received(
        database_session,
        user.id,
        waiting.id,
        850_023,
        now=due_at + timedelta(minutes=10),
    )
    assert received.work_item.status == "done"
    events = list(
        await database_session.scalars(
            select(WorkItemEvent).where(WorkItemEvent.user_id == user.id)
        )
    )
    assert {event.event_type for event in events} >= {
        "reminder_snoozed",
        "person_replied",
        "waiting_received",
    }


@pytest.mark.integration
async def test_reminder_done_callback_is_idempotent_and_owned(
    database_session: AsyncSession,
) -> None:
    telegram_user_id = 650_008
    user = await create_telegram_user(database_session, telegram_user_id)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Finish callback test",
        due_at=datetime(2026, 7, 24, 10, tzinfo=UTC),
    )
    reminder = (await reminders_for_item(database_session, item.id))[0]
    telegram_user = User(
        id=telegram_user_id,
        is_bot=False,
        first_name="Test",
    )
    message = Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=telegram_user_id, type="private"),
        from_user=telegram_user,
        text="Reminder",
    )
    callback = CallbackQuery(
        id="callback-1",
        from_user=telegram_user,
        chat_instance="test",
        message=message,
        data=f"rem:done:{reminder.id}",
    )
    update = Update(update_id=850_030, callback_query=callback)

    with (
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await reminder_callback(
            callback,
            update,
            database_session,
            30,
        )
        await reminder_callback(
            callback,
            update,
            database_session,
            30,
        )

    assert item.status == "done"
    assert item.completed_at is not None
    assert answer.await_count == 2
    assert (
        len(
            list(
                await database_session.scalars(
                    select(WorkItemEvent).where(
                        WorkItemEvent.telegram_update_id == update.update_id
                    )
                )
            )
        )
        == 1
    )
