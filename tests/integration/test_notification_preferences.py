# ruff: noqa: RUF001
from datetime import UTC, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.handlers.reminders import digest_callback
from flowmate.db.models import Reminder, WorkItemEvent
from flowmate.db.users import create_telegram_user
from flowmate.reminders.digests import (
    build_digest_snapshot,
    ensure_daily_digest_reminders,
    move_digest_items_to_tomorrow,
    prepare_digest_message,
)
from flowmate.reminders.enums import ReminderStatus, ReminderType
from flowmate.reminders.preferences import (
    NotificationDefaults,
    defer_due_reminders_for_quiet_hours,
    effective_preferences,
    get_effective_notification_preferences,
    get_or_create_notification_preferences,
)
from flowmate.reminders.service import (
    claim_due_reminders,
    create_reminder,
    get_claimed_reminder_delivery,
)
from flowmate.task_engine.action_sessions import create_action_session
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.service import create_work_item
from flowmate.workspaces import activate_workspace

DEFAULTS = NotificationDefaults(
    timezone="UTC",
    morning_digest_time=time(9),
    evening_digest_time=time(18),
    quiet_hours_start=time(22),
    quiet_hours_end=time(8),
    snooze_minutes=60,
)


@pytest.mark.integration
async def test_preferences_are_lazy_owned_and_default_to_opt_in(
    database_session: AsyncSession,
) -> None:
    owner = await create_telegram_user(database_session, 660_001)
    other = await create_telegram_user(database_session, 660_002)
    stored = await get_or_create_notification_preferences(
        database_session, owner.id, DEFAULTS
    )
    duplicate = await get_or_create_notification_preferences(
        database_session, owner.id, DEFAULTS
    )
    other_effective = await get_effective_notification_preferences(
        database_session, other.id, DEFAULTS
    )

    assert duplicate.user_id == stored.user_id
    assert stored.morning_digest_enabled is False
    assert stored.evening_digest_enabled is False
    assert stored.quiet_hours_enabled is False
    assert other_effective.timezone == "UTC"


@pytest.mark.integration
async def test_daily_digest_is_unique_and_uses_current_work_items(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 660_003)
    preferences = await get_or_create_notification_preferences(
        database_session, user.id, DEFAULTS
    )
    preferences.morning_digest_enabled = True
    now = datetime(2026, 7, 22, 10, tzinfo=UTC)
    await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Due today",
        due_at=datetime(2026, 7, 22, 12, tzinfo=UTC),
    )
    await create_work_item(
        database_session,
        user.id,
        item_type="question",
        title="Open question",
    )

    await ensure_daily_digest_reminders(database_session, now=now)
    await ensure_daily_digest_reminders(database_session, now=now)
    values = list(
        await database_session.scalars(
            select(Reminder).where(
                Reminder.user_id == user.id,
                Reminder.type == ReminderType.MORNING_DIGEST.value,
            )
        )
    )
    effective = effective_preferences(preferences, DEFAULTS)
    snapshot = await build_digest_snapshot(
        database_session,
        user.id,
        ReminderType.MORNING_DIGEST,
        now=now,
        preferences=effective,
    )
    message = await prepare_digest_message(
        database_session,
        user.id,
        ReminderType.MORNING_DIGEST,
        now=now,
        defaults=DEFAULTS,
    )

    assert len(values) == 1
    assert values[0].workspace == "personal"
    assert all(value.digest_local_date is not None for value in values)
    assert all(
        value.digest_local_date is not None
        and value.digest_local_date.isoformat() == "2026-07-22"
        for value in values
    )
    assert all(value.schedule_timezone == "UTC" for value in values)
    personal = next(
        value for value in snapshot.workspaces if value.workspace == "personal"
    )
    work = next(value for value in snapshot.workspaces if value.workspace == "work")
    assert [item.title for item in personal.items] == ["Due today", "Open question"]
    assert personal.total == 2
    assert work.empty
    assert message is not None
    assert "🏠 Личное" in message
    assert "🟠 Due today · 12:00" in message
    assert "❓ Open question" in message


@pytest.mark.integration
async def test_empty_digest_is_suppressed_unless_enabled(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 660_004)
    preferences = await get_or_create_notification_preferences(
        database_session, user.id, DEFAULTS
    )
    now = datetime(2026, 7, 22, 10, tzinfo=UTC)

    assert (
        await prepare_digest_message(
            database_session,
            user.id,
            ReminderType.EVENING_DIGEST,
            now=now,
            defaults=DEFAULTS,
        )
        is None
    )
    preferences.send_empty_digests = True
    preferences.evening_digest_enabled = True
    message = await prepare_digest_message(
        database_session,
        user.id,
        ReminderType.EVENING_DIGEST,
        now=now,
        defaults=DEFAULTS,
    )
    assert message is not None and "На сегодня всё спокойно." in message
    assert ": 0" not in message


@pytest.mark.integration
async def test_digest_limits_each_workspace_and_fills_with_fresh_inbox(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 660_010)
    now = datetime(2026, 7, 22, 10, tzinfo=UTC)
    activate_workspace(database_session, user_id=user.id, workspace="personal")
    personal_definitions = (
        ("Overdue urgent", "task", now - timedelta(days=2), "urgent"),
        ("Overdue normal", "task", now - timedelta(days=1), "normal"),
        ("Follow up", "follow_up", now - timedelta(hours=1), "normal"),
        ("Waiting", "waiting", now - timedelta(hours=2), "normal"),
        ("Due today", "task", now + timedelta(hours=2), "normal"),
        ("Question", "question", None, "normal"),
        ("Inbox", "task", None, "normal"),
    )
    for title, item_type, effective_at, priority in personal_definitions:
        await create_work_item(
            database_session,
            user.id,
            item_type=item_type,
            title=title,
            status="waiting" if item_type == "waiting" else "inbox",
            due_at=(
                effective_at if item_type not in {"follow_up", "question"} else None
            ),
            next_follow_up_at=effective_at if item_type == "follow_up" else None,
            waiting_since=(now - timedelta(days=3) if item_type == "waiting" else None),
            priority=priority,
        )

    activate_workspace(database_session, user_id=user.id, workspace="work")
    await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Work due",
        due_at=now + timedelta(hours=1),
    )
    await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Older work inbox",
    )
    await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Newer work inbox",
    )

    snapshot = await build_digest_snapshot(
        database_session,
        user.id,
        ReminderType.MORNING_DIGEST,
        now=now,
        preferences=effective_preferences(None, DEFAULTS),
    )
    personal = next(
        value for value in snapshot.workspaces if value.workspace == "personal"
    )
    work = next(value for value in snapshot.workspaces if value.workspace == "work")

    assert [item.title for item in personal.items] == [
        "Overdue urgent",
        "Overdue normal",
        "Follow up",
        "Waiting",
        "Due today",
    ]
    assert personal.total == 7
    assert [item.title for item in work.items] == [
        "Work due",
        "Newer work inbox",
        "Older work inbox",
    ]


@pytest.mark.integration
async def test_digest_scheduler_cancels_legacy_workspace_duplicate(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 660_011)
    preferences = await get_or_create_notification_preferences(
        database_session, user.id, DEFAULTS
    )
    preferences.morning_digest_enabled = True
    now = datetime(2026, 7, 22, 8, tzinfo=UTC)
    legacy = Reminder(
        user_id=user.id,
        workspace="work",
        type=ReminderType.MORNING_DIGEST.value,
        scheduled_at=datetime(2026, 7, 22, 9, tzinfo=UTC),
        schedule_kind="manual",
        status=ReminderStatus.PENDING.value,
        deduplication_key="digest:work:morning_digest:2026-07-22",
        digest_local_date=now.date(),
        schedule_timezone="UTC",
    )
    database_session.add(legacy)
    await database_session.flush()

    await ensure_daily_digest_reminders(database_session, now=now)
    values = list(
        await database_session.scalars(
            select(Reminder)
            .where(
                Reminder.user_id == user.id,
                Reminder.type == ReminderType.MORNING_DIGEST.value,
            )
            .execution_options(include_all_workspaces=True)
        )
    )

    assert len(values) == 2
    assert next(value for value in values if value.workspace == "work").status == (
        ReminderStatus.CANCELLED.value
    )
    canonical = next(value for value in values if value.workspace == "personal")
    assert canonical.status == ReminderStatus.PENDING.value
    assert canonical.deduplication_key == "digest:morning_digest:2026-07-22"


@pytest.mark.integration
async def test_quiet_hours_defer_without_consuming_delivery_attempt(
    database_session: AsyncSession,
) -> None:
    owner = await create_telegram_user(database_session, 660_005)
    other = await create_telegram_user(database_session, 660_006)
    preferences = await get_or_create_notification_preferences(
        database_session, owner.id, DEFAULTS
    )
    preferences.quiet_hours_enabled = True
    now = datetime(2026, 7, 22, 23, tzinfo=UTC)
    reminder, _ = await create_reminder(
        database_session,
        owner.id,
        reminder_type="custom",
        scheduled_at=now - timedelta(minutes=1),
        deduplication_key="quiet-owner",
        message="Safe message",
    )
    other_reminder, _ = await create_reminder(
        database_session,
        other.id,
        reminder_type="custom",
        scheduled_at=now - timedelta(minutes=1),
        deduplication_key="quiet-other",
        message="Safe message",
    )

    deferred = await defer_due_reminders_for_quiet_hours(
        database_session,
        now=now,
        defaults=DEFAULTS,
        limit=10,
    )

    assert deferred == 1
    assert reminder.status == ReminderStatus.SNOOZED.value
    assert reminder.delivery_attempts == 0
    assert reminder.snoozed_until is not None
    assert (
        datetime(2026, 7, 23, 8, tzinfo=UTC)
        <= reminder.snoozed_until
        < datetime(2026, 7, 23, 8, 2, tzinfo=UTC)
    )
    assert other_reminder.status == ReminderStatus.PENDING.value


@pytest.mark.integration
async def test_evening_bulk_move_is_atomic_idempotent_and_excludes_waiting(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 660_007)
    preferences = effective_preferences(None, DEFAULTS)
    task_date = datetime(2026, 7, 22, 15, tzinfo=UTC)
    follow_up_date = datetime(2026, 7, 22, 16, tzinfo=UTC)
    waiting_date = datetime(2026, 7, 22, 17, tzinfo=UTC)
    task = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Finish report",
        due_at=task_date,
    )
    follow_up = await create_work_item(
        database_session,
        user.id,
        item_type="follow_up",
        title="Call Anton",
        next_follow_up_at=follow_up_date,
    )
    waiting = await create_work_item(
        database_session,
        user.id,
        item_type="waiting",
        title="Wait for answer",
        status="waiting",
        due_at=waiting_date,
        waiting_since=waiting_date - timedelta(days=1),
    )

    count = await move_digest_items_to_tomorrow(
        database_session,
        user.id,
        local_date=task_date.date(),
        telegram_update_id=960_001,
        preferences=preferences,
        workspace="personal",
    )
    duplicate_count = await move_digest_items_to_tomorrow(
        database_session,
        user.id,
        local_date=task_date.date(),
        telegram_update_id=960_001,
        preferences=preferences,
        workspace="personal",
    )
    events = list(
        await database_session.scalars(
            select(WorkItemEvent).where(WorkItemEvent.user_id == user.id)
        )
    )

    assert count == 2
    assert duplicate_count == 0
    assert task.due_at == task_date + timedelta(days=1)
    assert follow_up.next_follow_up_at == follow_up_date + timedelta(days=1)
    assert waiting.due_at == waiting_date
    assert len([event for event in events if event.event_type == "rescheduled"]) == 2


@pytest.mark.integration
async def test_digest_action_chooses_workspace_without_switching_user(
    database_session: AsyncSession,
) -> None:
    telegram_user_id = 660_012
    user = await create_telegram_user(database_session, telegram_user_id)
    local_date = datetime(2026, 7, 22, 15, tzinfo=UTC)
    activate_workspace(database_session, user_id=user.id, workspace="personal")
    personal_item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Personal task",
        due_at=local_date,
    )
    activate_workspace(database_session, user_id=user.id, workspace="work")
    work_item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Work task",
        due_at=local_date,
    )
    activate_workspace(database_session, user_id=user.id, workspace="personal")
    reminder = Reminder(
        user_id=user.id,
        workspace="personal",
        type=ReminderType.EVENING_DIGEST.value,
        scheduled_at=local_date,
        schedule_kind="manual",
        status=ReminderStatus.SENT.value,
        deduplication_key="digest:evening_digest:2026-07-22",
        digest_local_date=local_date.date(),
        schedule_timezone="UTC",
        sent_at=local_date,
    )
    database_session.add(reminder)
    await database_session.flush()
    telegram_user = User(
        id=telegram_user_id,
        is_bot=False,
        first_name="Test",
    )
    message = Message(
        message_id=30,
        date=datetime.now(UTC),
        chat=Chat(id=telegram_user_id, type="private"),
        from_user=telegram_user,
        text="Digest",
    )
    choose_callback = CallbackQuery(
        id="digest-choose",
        from_user=telegram_user,
        chat_instance="test",
        message=message,
        data=f"dig:tomorrow:{reminder.id}",
    )
    choose_update = Update(update_id=970_010, callback_query=choose_callback)
    with (
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await digest_callback(
            choose_callback,
            choose_update,
            database_session,
            DEFAULTS,
            30,
        )
    answer_call = answer.await_args
    assert answer_call is not None
    chooser = answer_call.kwargs["reply_markup"]
    assert [button.text for button in chooser.inline_keyboard[0]] == [
        "Личное",
        "Работа",
    ]

    work_callback = CallbackQuery(
        id="digest-work",
        from_user=telegram_user,
        chat_instance="test",
        message=message,
        data=f"dig:tomorrow:w:{reminder.id}",
    )
    work_update = Update(update_id=970_011, callback_query=work_callback)
    with (
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        patch.object(Message, "answer", new_callable=AsyncMock),
    ):
        await digest_callback(
            work_callback,
            work_update,
            database_session,
            DEFAULTS,
            30,
        )

    assert personal_item.due_at == local_date
    assert work_item.due_at == local_date + timedelta(days=1)
    assert user.active_workspace == "personal"


@pytest.mark.integration
async def test_claim_time_quiet_check_covers_backlog_and_releases_after_quiet_hours(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 660_008)
    preferences = await get_or_create_notification_preferences(
        database_session, user.id, DEFAULTS
    )
    preferences.quiet_hours_enabled = True
    now = datetime(2026, 7, 22, 23, tzinfo=UTC)
    reminders: list[Reminder] = []
    for index in range(5):
        reminder, _ = await create_reminder(
            database_session,
            user.id,
            reminder_type="custom",
            scheduled_at=now - timedelta(minutes=1),
            deduplication_key=f"quiet-backlog-{index}",
            message="Safe message",
        )
        reminders.append(reminder)

    assert (
        await defer_due_reminders_for_quiet_hours(
            database_session,
            now=now,
            defaults=DEFAULTS,
            limit=4,
        )
        == 4
    )
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
            notification_defaults=DEFAULTS,
        )
        is None
    )
    assert all(value.status == ReminderStatus.SNOOZED.value for value in reminders)
    assert all(value.delivery_attempts == 0 for value in reminders)

    claimed_reminder = next(value for value in reminders if value.id == claims[0].id)
    release_at = claimed_reminder.snoozed_until
    assert release_at is not None
    released_claims = await claim_due_reminders(
        database_session,
        now=release_at,
        limit=5,
        max_attempts=3,
        processing_timeout=timedelta(minutes=5),
    )
    assert released_claims
    assert (
        await get_claimed_reminder_delivery(
            database_session,
            released_claims[0],
            now=release_at,
            notification_defaults=DEFAULTS,
        )
        is not None
    )


@pytest.mark.integration
async def test_digest_review_next_callback_is_idempotent(
    database_session: AsyncSession,
) -> None:
    telegram_user_id = 660_009
    user = await create_telegram_user(database_session, telegram_user_id)
    items = [
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title=f"Review item {index}",
        )
        for index in range(3)
    ]
    action_session = await create_action_session(
        database_session,
        user.id,
        action=WorkItemAction.DIGEST_REVIEW,
        ttl_minutes=30,
        work_item_id=items[0].id,
        context={
            "item_ids": [str(item.id) for item in items],
            "index": 0,
            "workspace": "personal",
        },
        telegram_update_id=970_001,
    )
    telegram_user = User(
        id=telegram_user_id,
        is_bot=False,
        first_name="Test",
    )
    message = Message(
        message_id=20,
        date=datetime.now(UTC),
        chat=Chat(id=telegram_user_id, type="private"),
        from_user=telegram_user,
        text="Digest",
    )
    callback = CallbackQuery(
        id="digest-next",
        from_user=telegram_user,
        chat_instance="test",
        message=message,
        data=f"dig:next:{action_session.id}",
    )
    update = Update(update_id=970_002, callback_query=callback)

    with (
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        patch(
            "flowmate.bot.handlers.reminders._send_review_item",
            new_callable=AsyncMock,
        ) as send_review_item,
    ):
        await digest_callback(callback, update, database_session, DEFAULTS, 30)
        await digest_callback(callback, update, database_session, DEFAULTS, 30)

    await database_session.refresh(action_session)
    assert action_session.context["index"] == 1
    assert action_session.context["processed_update_ids"] == [update.update_id]
    send_review_item.assert_awaited_once()
