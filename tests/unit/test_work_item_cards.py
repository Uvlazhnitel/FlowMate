from datetime import UTC, datetime, time
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import (
    DraftItemType,
    ManagementAction,
    ManagementIntent,
)
from flowmate.bot.handlers.work_items.cards import (
    details_keyboard,
    encode_revision,
    format_work_item_details,
    parse_work_item_callback,
    refresh_work_item_card,
)
from flowmate.bot.handlers.work_items.editing import edit_options_keyboard
from flowmate.db.models import Note, WorkItem, WorkItemEvent
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.task_engine.details import WorkItemDetails


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


def test_work_item_callback_parser_is_strict() -> None:
    item_id = uuid4()

    assert parse_work_item_callback(f"wi:postpone:{item_id}:3") == (
        "postpone",
        item_id,
        "3",
        None,
    )
    assert parse_work_item_callback(f"wi:details:{item_id}") == (
        "details",
        item_id,
        None,
        None,
    )
    assert parse_work_item_callback(f"wi:details:{item_id}:w") == (
        "details",
        item_id,
        None,
        "work",
    )
    assert parse_work_item_callback("wi:details:not-a-uuid") is None
    assert parse_work_item_callback("draft:details:value") is None


def test_detail_card_is_safe_concise_and_context_sensitive() -> None:
    details = make_details()
    text = format_work_item_details(details, ZoneInfo("UTC"))
    keyboard = details_keyboard(details)

    assert "📌 <b>Задача</b>\nImportant work" in text
    assert "Private linked note" in text
    assert "Testing" not in text and "Антон" not in text
    assert len(text) < 4000
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == [
        "✅ Выполнено",
        "⏰ Отложить",
        "📅 Перенести",
        "📝 Заметка",
        "❌ Отменить",
        "✏️ Изменить",
        "📖 История",
    ]
    assert all(
        len(button.callback_data or "") <= 64
        for row in keyboard.inline_keyboard
        for button in row
    )


@pytest.mark.parametrize(
    ("item_type", "expected"),
    [
        ("follow_up", {"✅ Выполнено", "💬 Ответ получен", "⏰ Отложить"}),
        ("waiting", {"✅ Получено", "🔁 Сделать follow-up", "⏰ Отложить"}),
    ],
)
def test_detail_actions_follow_work_item_type(
    item_type: str,
    expected: set[str],
) -> None:
    keyboard = details_keyboard(make_details(item_type))
    labels = {button.text for row in keyboard.inline_keyboard for button in row}
    assert expected <= labels


def test_completed_detail_has_only_reopen_and_history() -> None:
    keyboard = details_keyboard(make_details(status="done"))
    assert [button.text for row in keyboard.inline_keyboard for button in row] == [
        "↩️ Вернуть",
        "📖 История",
    ]


def test_edit_menu_exposes_content_date_and_clear_actions() -> None:
    details = make_details()

    keyboard = edit_options_keyboard(details.item)

    assert [button.text for row in keyboard.inline_keyboard for button in row] == [
        "Название",
        "Описание",
        "Дата",
        "Очистить описание",
        "Назад",
    ]


@pytest.mark.asyncio
async def test_card_refresh_sends_new_card_when_edit_fails() -> None:
    details = make_details()
    message = make_message("card")
    session = MagicMock(spec=AsyncSession)
    telegram_error = TelegramAPIError(
        method=MagicMock(),
        message="message cannot be edited",
    )
    with patch(
        "flowmate.bot.handlers.work_items.cards.send_details",
        new=AsyncMock(side_effect=(telegram_error, True)),
    ) as send:
        await refresh_work_item_card(
            message,
            cast(AsyncSession, session),
            details.item.user_id,
            details.item,
            ZoneInfo("UTC"),
        )

    assert send.await_count == 2
    assert send.await_args_list[0].kwargs["edit"] is True
    assert "edit" not in send.await_args_list[1].kwargs
