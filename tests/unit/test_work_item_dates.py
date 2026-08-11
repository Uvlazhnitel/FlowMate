from datetime import UTC, datetime, time
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import (
    DraftItemType,
    ManagementAction,
    ManagementIntent,
)
from flowmate.bot.handlers.work_items.callbacks import work_item_callback
from flowmate.bot.handlers.work_items.cards import (
    encode_revision,
    format_datetime,
    parse_work_item_callback,
)
from flowmate.bot.handlers.work_items.dates import (
    parse_user_datetime,
    reschedule_options_keyboard,
)
from flowmate.bot.handlers.work_items.sessions import action_session_message
from flowmate.db.models import Note, WorkItem, WorkItemActionSession, WorkItemEvent
from flowmate.reminders.parsing import SnoozeParsingService
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.task_engine.details import WorkItemDetails
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.rescheduling import ReschedulePreset, ReschedulingService


def make_message(text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=123, type="private"),
        from_user=User(id=123, is_bot=False, first_name="Test"),
        text=text,
    )


def make_intent() -> ManagementIntent:
    return ManagementIntent(
        action=ManagementAction.COMPLETE,
        target_type=DraftItemType.FOLLOW_UP,
        record_query="Антон",
        contextual_reference=False,
        person_candidate="Антон",
        topic_candidate=None,
        note_text=None,
        temporal_candidate=None,
        missing_fields=[],
        ambiguities=[],
        confidence=0.95,
    )


def make_details(item_type: str = "task", status: str = "inbox") -> WorkItemDetails:
    now = datetime(2026, 7, 22, 9, tzinfo=UTC)
    user_id = uuid4()
    item = WorkItem(
        id=uuid4(),
        user_id=user_id,
        type=item_type,
        title="  Important\nwork  ",
        description="Description " * 200,
        status=status,
        priority="normal",
        updated_at=now,
    )
    note = Note(
        id=uuid4(),
        user_id=user_id,
        content="Private linked note",
        source="manual",
        created_at=now,
    )
    event = WorkItemEvent(
        id=uuid4(),
        user_id=user_id,
        work_item_id=item.id,
        event_type="created",
        payload={},
        created_at=now,
    )
    return WorkItemDetails(
        item=item,
        topic_name="Testing",
        person_names=("Антон",),
        notes=(note,),
        events=(event,),
        nearest_reminder=None,
    )


def notification_defaults() -> NotificationDefaults:
    return NotificationDefaults(
        timezone="UTC",
        morning_digest_time=time(9),
        evening_digest_time=time(18),
        quiet_hours_start=time(22),
        quiet_hours_end=time(8),
        snooze_minutes=60,
    )


def make_callback(item: WorkItem, action: str = "c") -> tuple[CallbackQuery, Update]:
    message = make_message("card")
    revision = encode_revision(int(item.updated_at.timestamp() * 1_000_000))
    callback = CallbackQuery(
        id="callback-id",
        from_user=cast(User, message.from_user),
        chat_instance="test",
        message=message,
        data=f"wi:{action}:{item.id}:{revision}",
    )
    return callback, Update(update_id=9100, callback_query=callback)


def test_custom_date_parser_uses_application_timezone() -> None:
    timezone = ZoneInfo("Europe/Riga")

    date_only = parse_user_datetime("21.07.2026", timezone)
    with_time = parse_user_datetime("2026-07-22 09:30", timezone)

    assert date_only == datetime(2026, 7, 21, 23, 59, 59, tzinfo=timezone)
    assert with_time == datetime(2026, 7, 22, 9, 30, tzinfo=timezone)
    assert parse_user_datetime("в следующую пятницу", timezone) is None
    assert format_datetime(with_time, timezone) == "22 июля, 09:30"


def test_reschedule_keyboard_contains_all_presets() -> None:
    details = make_details("follow_up")
    details.item.next_follow_up_at = datetime(2026, 7, 24, 14, tzinfo=UTC)
    now = datetime(2026, 7, 24, 18, tzinfo=UTC)

    keyboard = reschedule_options_keyboard(details.item, now)
    labels = {button.text for row in keyboard.inline_keyboard for button in row}

    assert {
        "Позже сегодня",
        "Завтра утром",
        "Следующий рабочий день",
        "Через неделю",
        "Другая дата",
        "Отмена",
    } == labels
    revision = encode_revision(int(details.item.updated_at.timestamp() * 1_000_000))
    parsed_callbacks = [
        parsed
        for row in keyboard.inline_keyboard
        for button in row
        if button.text != "Отмена"
        and (parsed := parse_work_item_callback(button.callback_data)) is not None
    ]
    assert any(parsed[2] == revision for parsed in parsed_callbacks)


@pytest.mark.asyncio
async def test_reschedule_preset_callback_uses_shared_service() -> None:
    details = make_details()
    callback, update = make_callback(details.item, "rn")
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    target = datetime(2026, 7, 29, 14, tzinfo=UTC)
    details.item.due_at = target
    preferences = SimpleNamespace(
        zoneinfo=ZoneInfo("UTC"),
        default_reminder_time=time(8, 30),
        default_snooze_minutes=60,
    )
    service = MagicMock(spec=ReschedulingService)
    service.reschedule_preset = AsyncMock(
        return_value=SimpleNamespace(work_item=details.item, changed=True)
    )

    with (
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=details.item.user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_work_item",
            new=AsyncMock(return_value=details.item),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_active_action_session",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_effective_notification_preferences",
            new=AsyncMock(return_value=preferences),
        ),
        patch(
            "flowmate.bot.handlers.work_items.cards.send_details",
            new=AsyncMock(return_value=True),
        ),
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock),
        patch.object(Message, "answer", new_callable=AsyncMock),
    ):
        await work_item_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
            notification_defaults(),
            rescheduling_service=cast(ReschedulingService, service),
        )

    service.reschedule_preset.assert_awaited_once()
    call = service.reschedule_preset.await_args
    assert call is not None
    assert call.args[4] is ReschedulePreset.NEXT_WEEK
    assert call.kwargs["preferences"] is preferences
    cast(AsyncMock, session.commit).assert_awaited_once()


@pytest.mark.asyncio
async def test_reschedule_text_reply_uses_shared_service() -> None:
    message = make_message("в пятницу после обеда")
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    item = make_details().item
    item.due_at = datetime(2026, 7, 24, 15, tzinfo=UTC)
    action = WorkItemActionSession(
        id=uuid4(),
        user_id=item.user_id,
        work_item_id=item.id,
        action=WorkItemAction.RESCHEDULE.value,
        status="open",
        context={"work_item_revision": 12},
        expires_at=datetime.now(UTC),
    )
    preferences = SimpleNamespace(
        zoneinfo=ZoneInfo("UTC"),
        default_reminder_time=time(8, 30),
    )
    service = MagicMock(spec=ReschedulingService)
    service.reschedule_text = AsyncMock(
        return_value=SimpleNamespace(work_item=item, changed=True)
    )

    with (
        patch(
            "flowmate.bot.handlers.work_items.sessions.get_effective_notification_preferences",
            new=AsyncMock(return_value=preferences),
        ),
        patch(
            "flowmate.bot.handlers.work_items.sessions.finish_action_session",
            new=AsyncMock(),
        ),
        patch(
            "flowmate.bot.handlers.work_items.sessions.answer_with_main_menu",
            new=AsyncMock(),
        ),
    ):
        await action_session_message(
            message,
            cast(Bot, MagicMock(spec=Bot)),
            Update(update_id=9200, message=message),
            cast(AsyncSession, session),
            action,
            item.user_id,
            ZoneInfo("UTC"),
            notification_defaults(),
            SnoozeParsingService(None, timeout_seconds=1),
            None,
            rescheduling_service=cast(ReschedulingService, service),
        )

    service.reschedule_text.assert_awaited_once()
    call = service.reschedule_text.await_args
    assert call is not None
    assert call.args[4] == "в пятницу после обеда"
    assert call.kwargs["expected_revision"] == 12
    cast(AsyncMock, session.commit).assert_awaited_once()
