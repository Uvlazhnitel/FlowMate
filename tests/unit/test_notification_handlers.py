from datetime import UTC, datetime, time, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User, Voice
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.handlers.preferences import reminders_settings_command
from flowmate.bot.handlers.reminders import reminder_callback
from flowmate.bot.handlers.work_items.sessions import action_session_message
from flowmate.db.models import User as DatabaseUser
from flowmate.db.models import (
    UserNotificationPreferences,
    WorkItem,
    WorkItemActionSession,
)
from flowmate.reminders.parsing import SnoozeParsingService
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.speech.service import TranscriptionService
from flowmate.task_engine.enums import WorkItemAction

DEFAULTS = NotificationDefaults(
    timezone="UTC",
    morning_digest_time=time(9),
    evening_digest_time=time(18),
    quiet_hours_start=time(22),
    quiet_hours_end=time(8),
    snooze_minutes=60,
)


def make_message(text: str | None = None, *, voice: Voice | None = None) -> Message:
    return Message(
        message_id=10,
        date=datetime.now(UTC),
        chat=Chat(id=123, type="private"),
        from_user=User(id=123, is_bot=False, first_name="Test"),
        text=text,
        voice=voice,
    )


@pytest.mark.asyncio
async def test_reminders_command_enables_morning_digest() -> None:
    message = make_message("/reminders morning 08:30")
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    user = DatabaseUser(id=uuid4(), telegram_user_id=123)
    preferences = UserNotificationPreferences(
        user_id=user.id,
        timezone="UTC",
        morning_digest_enabled=False,
        morning_digest_time=time(9),
        evening_digest_enabled=False,
        evening_digest_time=time(18),
        quiet_hours_enabled=False,
        quiet_hours_start=time(22),
        quiet_hours_end=time(8),
        default_reminder_time=time(9),
        default_snooze_minutes=60,
        send_empty_digests=False,
    )
    with (
        patch(
            "flowmate.bot.handlers.preferences.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.preferences.get_or_create_notification_preferences",
            new=AsyncMock(return_value=preferences),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await reminders_settings_command(
            message,
            cast(AsyncSession, session),
            DEFAULTS,
        )

    assert preferences.morning_digest_enabled is True
    assert preferences.morning_digest_time == time(8, 30)
    session.commit.assert_awaited_once()
    answer_call = answer.await_args
    assert answer_call is not None
    assert "Утренний обзор: 08:30" in answer_call.args[0]


@pytest.mark.asyncio
async def test_voice_custom_snooze_updates_reminder_without_note_flow() -> None:
    voice = Voice(
        file_id="voice-file",
        file_unique_id="voice-unique",
        duration=3,
        file_size=100,
    )
    message = make_message(voice=voice)
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    transcription = MagicMock(spec=TranscriptionService)
    transcription.is_too_large.return_value = False
    transcription.transcribe = AsyncMock(return_value="завтра в девять")
    snooze_parser = MagicMock(spec=SnoozeParsingService)
    target = datetime(2026, 7, 23, 9, tzinfo=UTC)
    snooze_parser.parse = AsyncMock(return_value=target)
    action = WorkItemActionSession(
        id=uuid4(),
        user_id=uuid4(),
        work_item_id=uuid4(),
        action=WorkItemAction.REMINDER_SNOOZE.value,
        status="open",
        context={"reminder_id": str(uuid4())},
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    with (
        patch(
            "flowmate.bot.handlers.work_items.sessions.get_effective_notification_preferences",
            new=AsyncMock(return_value=MagicMock(zoneinfo=ZoneInfo("UTC"))),
        ),
        patch(
            "flowmate.bot.handlers.work_items.sessions.snooze_work_item_reminder",
            new=AsyncMock(return_value=(MagicMock(), True)),
        ) as snooze,
        patch(
            "flowmate.bot.handlers.work_items.sessions.finish_action_session",
            new=AsyncMock(),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock),
    ):
        await action_session_message(
            message,
            cast(Bot, MagicMock(spec=Bot)),
            Update(update_id=970_001, message=message),
            cast(AsyncSession, session),
            action,
            action.user_id,
            ZoneInfo("UTC"),
            DEFAULTS,
            cast(SnoozeParsingService, snooze_parser),
            cast(TranscriptionService, transcription),
        )

    transcription.transcribe.assert_awaited_once()
    snooze_parser.parse.assert_awaited_once()
    snooze_call = snooze.await_args
    assert snooze_call is not None
    assert snooze_call.kwargs["until"] == target
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reminder_tomorrow_button_uses_default_reminder_time() -> None:
    message = make_message("reminder")
    reminder_id = uuid4()
    callback = CallbackQuery(
        id="callback-id",
        from_user=cast(User, message.from_user),
        chat_instance="test",
        message=message,
        data=f"rem:snoozetomorrow:{reminder_id}",
    )
    update = Update(update_id=970_002, callback_query=callback)
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    user = DatabaseUser(id=uuid4(), telegram_user_id=123)
    item = WorkItem(
        id=uuid4(),
        user_id=user.id,
        type="task",
        title="Test",
        status="active",
        priority="normal",
    )
    preferences = MagicMock(
        zoneinfo=ZoneInfo("UTC"),
        default_reminder_time=time(7, 45),
        default_snooze_minutes=60,
    )
    with (
        patch(
            "flowmate.bot.handlers.reminders.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.reminders.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.reminders.get_reminder_action_target",
            new=AsyncMock(return_value=MagicMock(work_item=item)),
        ),
        patch(
            "flowmate.bot.handlers.reminders.get_effective_notification_preferences",
            new=AsyncMock(return_value=preferences),
        ),
        patch(
            "flowmate.bot.handlers.reminders.snooze_work_item_reminder",
            new=AsyncMock(return_value=(MagicMock(), True)),
        ) as snooze,
        patch.object(Message, "answer", new_callable=AsyncMock),
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
    ):
        await reminder_callback(
            callback,
            update,
            cast(AsyncSession, session),
            30,
            DEFAULTS,
        )

    snooze_call = snooze.await_args
    assert snooze_call is not None
    until = snooze_call.kwargs["until"]
    assert isinstance(until, datetime)
    assert until.astimezone(ZoneInfo("UTC")).time() == time(7, 45)
    session.commit.assert_awaited_once()
