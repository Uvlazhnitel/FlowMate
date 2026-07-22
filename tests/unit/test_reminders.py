import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from flowmate.bot.handlers.reminders import (
    parse_reminder_callback,
    snooze_keyboard,
)
from flowmate.core.config import Settings
from flowmate.reminders.enums import ReminderScheduleKind, ReminderType
from flowmate.reminders.notifications import (
    PermanentNotificationError,
    ReminderNotification,
    TelegramNotificationService,
    TemporaryNotificationError,
    build_reminder_notification,
)
from flowmate.reminders.processor import ReminderProcessor
from flowmate.reminders.service import ClaimedReminder, ReminderDelivery
from flowmate.scheduler.app import create_scheduler, run_scheduler

NOW = datetime(2026, 7, 23, 10, tzinfo=UTC)


def make_delivery(
    reminder_type: ReminderType = ReminderType.DEADLINE,
) -> ReminderDelivery:
    return ReminderDelivery(
        reminder_id=uuid4(),
        processing_token=uuid4(),
        reminder_type=reminder_type,
        schedule_kind=ReminderScheduleKind.EXACT,
        telegram_user_id=123,
        work_item_id=uuid4(),
        work_item_title="Prepare release",
        topic_name="Release",
        person_names=("Anton",),
        scheduled_at=NOW,
        reference_at=NOW,
        message="Custom text",
    )


def test_notification_formatting_is_plain_and_bounded() -> None:
    deadline = build_reminder_notification(make_delivery())
    custom = build_reminder_notification(make_delivery(ReminderType.CUSTOM))
    digest = build_reminder_notification(make_delivery(ReminderType.MORNING_DIGEST))

    assert "⏰ Срок" in deadline.text
    assert "Anton — Prepare release" in deadline.text
    assert "Тема: Release" in deadline.text
    assert deadline.reply_markup is not None
    assert custom.text == "Custom text"
    assert digest.text == "Custom text"
    assert digest.reply_markup is not None


def test_reminder_callback_data_and_snooze_options() -> None:
    reminder_id = uuid4()

    assert parse_reminder_callback(f"rem:done:{reminder_id}") == (
        "done",
        reminder_id,
    )
    assert parse_reminder_callback("rem:done:not-a-uuid") is None
    assert parse_reminder_callback("wi:done:not-a-uuid") is None
    keyboard = snooze_keyboard(reminder_id)
    assert [button.text for button in keyboard.inline_keyboard[0]] == [
        "15 минут",
        "1 час",
        "3 часа",
    ]
    assert keyboard.inline_keyboard[-1][0].text == "Другая дата"


@pytest.mark.parametrize(
    ("reminder_type", "heading", "button"),
    [
        (ReminderType.FOLLOW_UP, "🔁 Follow-up", "Ответ получен"),
        (ReminderType.WAITING, "⏳ Ожидание", "Получено"),
    ],
)
def test_work_item_notifications_include_context_overdue_and_actions(
    reminder_type: ReminderType,
    heading: str,
    button: str,
) -> None:
    notification = build_reminder_notification(
        make_delivery(reminder_type),
        now=NOW + timedelta(hours=1),
    )

    assert heading in notification.text
    assert "Anton — Prepare release" in notification.text
    assert "Тема: Release" in notification.text
    assert "Просрочено" in notification.text
    assert notification.reply_markup is not None
    assert button in {
        value.text for row in notification.reply_markup.inline_keyboard for value in row
    }


@pytest.mark.asyncio
async def test_telegram_notification_maps_temporary_and_permanent_errors() -> None:
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock(
        side_effect=TelegramRetryAfter(
            SendMessage(chat_id=123, text="test"),
            "retry",
            17,
        )
    )
    service = TelegramNotificationService(cast(Bot, bot))

    with pytest.raises(TemporaryNotificationError) as retry_error:
        await service.send(ReminderNotification(123, "test"))
    assert retry_error.value.code == "telegram_retry_after"
    assert retry_error.value.retry_after_seconds == 17

    bot.send_message = AsyncMock(
        side_effect=TelegramForbiddenError(
            SendMessage(chat_id=123, text="test"),
            "private provider detail",
        )
    )
    with pytest.raises(PermanentNotificationError) as forbidden_error:
        await service.send(ReminderNotification(123, "test"))
    assert forbidden_error.value.code == "telegram_forbidden"


def fake_session_factory() -> async_sessionmaker[AsyncSession]:
    return cast(async_sessionmaker[AsyncSession], MagicMock())


@asynccontextmanager
async def fake_session_scope(
    _factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    yield cast(AsyncSession, MagicMock(spec=AsyncSession))


@pytest.mark.asyncio
async def test_processor_claims_sends_and_marks_with_mocked_clock() -> None:
    claim = ClaimedReminder(uuid4(), uuid4())
    delivery = make_delivery()
    delivery = replace(
        delivery,
        reminder_id=claim.id,
        processing_token=claim.processing_token,
    )
    notifier = MagicMock()
    notifier.send = AsyncMock()
    processor = ReminderProcessor(
        fake_session_factory(),
        notifier,
        batch_size=10,
        max_attempts=3,
        retry_delay=timedelta(minutes=1),
        processing_timeout=timedelta(minutes=5),
        delivery_timeout_seconds=10,
        clock=lambda: NOW,
    )
    with (
        patch(
            "flowmate.reminders.processor.session_scope",
            new=fake_session_scope,
        ),
        patch(
            "flowmate.reminders.processor.claim_due_reminders",
            new=AsyncMock(return_value=[claim]),
        ) as claim_due,
        patch(
            "flowmate.reminders.processor.ensure_daily_digest_reminders",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "flowmate.reminders.processor.defer_due_reminders_for_quiet_hours",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "flowmate.reminders.processor.get_claimed_reminder_delivery",
            new=AsyncMock(return_value=delivery),
        ),
        patch(
            "flowmate.reminders.processor.mark_reminder_sent",
            new=AsyncMock(return_value=True),
        ) as mark_sent,
    ):
        await processor.process_due_reminders()

    claim_due.assert_awaited_once()
    notifier.send.assert_awaited_once()
    mark_sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_processor_uses_retry_after_and_permanent_failure() -> None:
    claim = ClaimedReminder(uuid4(), uuid4())
    delivery = make_delivery()
    delivery = replace(
        delivery,
        reminder_id=claim.id,
        processing_token=claim.processing_token,
    )
    notifier = MagicMock()
    notifier.send = AsyncMock(
        side_effect=TemporaryNotificationError("telegram_retry_after", 120)
    )
    processor = ReminderProcessor(
        fake_session_factory(),
        notifier,
        batch_size=10,
        max_attempts=3,
        retry_delay=timedelta(seconds=60),
        processing_timeout=timedelta(minutes=5),
        delivery_timeout_seconds=10,
        clock=lambda: NOW,
    )
    failure = AsyncMock(return_value=True)
    with (
        patch("flowmate.reminders.processor.session_scope", new=fake_session_scope),
        patch(
            "flowmate.reminders.processor.get_claimed_reminder_delivery",
            new=AsyncMock(return_value=delivery),
        ),
        patch(
            "flowmate.reminders.processor.mark_reminder_delivery_failure",
            new=failure,
        ),
    ):
        await processor._process_claim(claim)

    temporary_call = failure.await_args
    assert temporary_call is not None
    assert temporary_call.kwargs["permanent"] is False
    assert temporary_call.kwargs["retry_delay"] == timedelta(seconds=120)

    notifier.send = AsyncMock(
        side_effect=PermanentNotificationError("telegram_forbidden")
    )
    failure.reset_mock()
    with (
        patch("flowmate.reminders.processor.session_scope", new=fake_session_scope),
        patch(
            "flowmate.reminders.processor.get_claimed_reminder_delivery",
            new=AsyncMock(return_value=delivery),
        ),
        patch(
            "flowmate.reminders.processor.mark_reminder_delivery_failure",
            new=failure,
        ),
    ):
        await processor._process_claim(claim)

    permanent_call = failure.await_args
    assert permanent_call is not None
    assert permanent_call.kwargs["permanent"] is True


def test_scheduler_job_is_immediate_single_instance_interval() -> None:
    processor = MagicMock(spec=ReminderProcessor)
    processor.process_due_reminders = AsyncMock()
    scheduler = create_scheduler(processor, interval_seconds=23)

    job = scheduler.get_job("process_due_reminders")

    assert job is not None
    assert job.max_instances == 1
    assert job.coalesce is True
    assert job.trigger.interval.total_seconds() == 23
    assert job.next_run_time is not None


@pytest.mark.asyncio
async def test_scheduler_missing_token_fails_before_database_creation() -> None:
    settings = Settings(_env_file=None, telegram_bot_token=None)
    with patch("flowmate.scheduler.app.create_engine") as create_engine:
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            await run_scheduler(settings, stop_event=asyncio.Event())

    create_engine.assert_not_called()
