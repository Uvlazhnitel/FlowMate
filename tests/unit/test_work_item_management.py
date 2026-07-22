# ruff: noqa: RUF001
from datetime import UTC, datetime, time
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import (
    DraftItemType,
    ManagementAction,
    ManagementIntent,
)
from flowmate.bot.handlers.navigation import today_command
from flowmate.bot.handlers.work_items import (
    apply_management_intent,
    details_keyboard,
    encode_revision,
    execute_management_intent,
    format_datetime,
    format_work_item_details,
    next_working_day,
    parse_user_datetime,
    parse_work_item_callback,
    reschedule_options_keyboard,
    start_input_session,
    work_item_callback,
    work_item_selection_callback,
)
from flowmate.db.models import Note, WorkItem, WorkItemEvent
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.task_engine.details import WorkItemDetails
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.management import StaleWorkItemError


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


def test_work_item_callback_parser_is_strict() -> None:
    item_id = uuid4()

    assert parse_work_item_callback(f"wi:postpone:{item_id}:3") == (
        "postpone",
        item_id,
        "3",
    )
    assert parse_work_item_callback(f"wi:details:{item_id}") == (
        "details",
        item_id,
        None,
    )
    assert parse_work_item_callback("wi:details:not-a-uuid") is None
    assert parse_work_item_callback("draft:details:value") is None


def test_custom_date_parser_uses_application_timezone() -> None:
    timezone = ZoneInfo("Europe/Riga")

    date_only = parse_user_datetime("21.07.2026", timezone)
    with_time = parse_user_datetime("2026-07-22 09:30", timezone)

    assert date_only == datetime(2026, 7, 21, 23, 59, 59, tzinfo=timezone)
    assert with_time == datetime(2026, 7, 22, 9, 30, tzinfo=timezone)
    assert parse_user_datetime("в следующую пятницу", timezone) is None
    assert format_datetime(with_time, timezone) == "22.07.2026 09:30"


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


def test_detail_card_is_safe_concise_and_context_sensitive() -> None:
    details = make_details()
    text = format_work_item_details(details, ZoneInfo("UTC"))
    keyboard = details_keyboard(details)

    assert "Название: Important work" in text
    assert "Private linked note" in text
    assert "Testing" in text and "Антон" in text
    assert len(text) < 4000
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert labels == [
        "✅ Выполнено",
        "⏰ Отложить",
        "📅 Перенести",
        "📝 Заметка",
        "❌ Отменить",
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


def test_reschedule_presets_use_local_working_days() -> None:
    details = make_details("follow_up")
    details.item.next_follow_up_at = datetime(2026, 7, 24, 14, tzinfo=UTC)
    now = datetime(2026, 7, 24, 18, tzinfo=UTC)

    target = next_working_day(
        now,
        details.item,
        ZoneInfo("UTC"),
        datetime.min.time().replace(hour=9),
    )
    keyboard = reschedule_options_keyboard(details.item, now)

    assert target == datetime(2026, 7, 27, 14, tzinfo=UTC)
    assert "Позже сегодня" in {
        button.text for row in keyboard.inline_keyboard for button in row
    }
    revision = encode_revision(int(details.item.updated_at.timestamp() * 1_000_000))
    assert any(
        (button.callback_data or "").endswith(revision)
        for row in keyboard.inline_keyboard
        for button in row
        if button.text != "Отмена"
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


@pytest.mark.asyncio
async def test_complete_callback_commits_and_refreshes_card() -> None:
    details = make_details()
    callback, update = make_callback(details.item)
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    preferences = SimpleNamespace(
        zoneinfo=ZoneInfo("UTC"),
        morning_digest_time=time(9),
        default_snooze_minutes=60,
    )
    with (
        patch(
            "flowmate.bot.handlers.work_items.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=details.item.user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_work_item",
            new=AsyncMock(return_value=details.item),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_active_action_session",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_effective_notification_preferences",
            new=AsyncMock(return_value=preferences),
        ),
        patch(
            "flowmate.bot.handlers.work_items.complete_work_item",
            new=AsyncMock(return_value=SimpleNamespace(changed=True)),
        ) as complete,
        patch(
            "flowmate.bot.handlers.work_items.send_details",
            new=AsyncMock(return_value=True),
        ) as refresh,
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
    ):
        await work_item_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
            notification_defaults(),
        )

    complete.assert_awaited_once()
    complete_call = complete.await_args
    assert complete_call is not None
    assert complete_call.kwargs["expected_revision"] == int(
        details.item.updated_at.timestamp() * 1_000_000
    )
    cast(AsyncMock, session.commit).assert_awaited_once()
    refresh.assert_awaited_once()
    refresh_call = refresh.await_args
    assert refresh_call is not None
    assert refresh_call.kwargs["edit"] is True
    answer.assert_awaited_once_with("Запись завершена.")


@pytest.mark.asyncio
async def test_stale_callback_only_refreshes_current_card() -> None:
    details = make_details()
    callback, update = make_callback(details.item)
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=details.item.user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_work_item",
            new=AsyncMock(return_value=details.item),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_active_action_session",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_effective_notification_preferences",
            new=AsyncMock(return_value=SimpleNamespace()),
        ),
        patch(
            "flowmate.bot.handlers.work_items.complete_work_item",
            new=AsyncMock(side_effect=StaleWorkItemError("stale")),
        ),
        patch(
            "flowmate.bot.handlers.work_items.send_details",
            new=AsyncMock(return_value=True),
        ) as refresh,
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
    ):
        await work_item_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
            notification_defaults(),
        )

    cast(AsyncMock, session.commit).assert_not_awaited()
    cast(AsyncMock, session.rollback).assert_awaited_once()
    refresh.assert_awaited_once()
    answer.assert_awaited_once_with("Карточка обновлена.", show_alert=True)


@pytest.mark.asyncio
async def test_active_draft_blocks_inline_mutation() -> None:
    details = make_details()
    callback, update = make_callback(details.item)
    session = MagicMock(spec=AsyncSession)
    with (
        patch(
            "flowmate.bot.handlers.work_items.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=details.item.user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_work_item",
            new=AsyncMock(return_value=details.item),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_active_draft_for_user",
            new=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ),
        patch(
            "flowmate.bot.handlers.work_items.complete_work_item",
            new=AsyncMock(),
        ) as complete,
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
    ):
        await work_item_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
            notification_defaults(),
        )

    complete.assert_not_awaited()
    answer.assert_awaited_once_with(
        "Сначала завершите или отмените активный черновик.", show_alert=True
    )


@pytest.mark.asyncio
async def test_high_confidence_management_executes_single_match() -> None:
    message = make_message("закрой follow-up с Антоном")
    update = Update(update_id=9001, message=message)
    user_id = uuid4()
    item = WorkItem(
        id=uuid4(),
        user_id=user_id,
        type="follow_up",
        title="Связаться с Антоном",
        status="inbox",
        priority="normal",
    )
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.find_intent_targets",
            new=AsyncMock(return_value=[item]),
        ),
        patch(
            "flowmate.bot.handlers.work_items.complete_work_item",
            new=AsyncMock(),
        ) as complete,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        handled = await execute_management_intent(
            message,
            update,
            cast(AsyncSession, session),
            make_intent(),
            high_confidence_threshold=0.8,
            action_ttl_minutes=30,
            app_timezone=ZoneInfo("UTC"),
        )

    assert handled is True
    complete.assert_awaited_once_with(
        cast(AsyncSession, session),
        user_id,
        item.id,
        telegram_update_id=9001,
    )
    cast(AsyncMock, session.commit).assert_awaited_once()
    answer.assert_awaited_once_with("Запись завершена.")


@pytest.mark.asyncio
async def test_ambiguous_management_creates_selection_without_mutation() -> None:
    message = make_message("закрой follow-up с Антоном")
    update = Update(update_id=9002, message=message)
    user_id = uuid4()
    items = [
        WorkItem(
            id=uuid4(),
            user_id=user_id,
            type="follow_up",
            title=f"Follow-up {index}",
            status="inbox",
            priority="normal",
        )
        for index in range(2)
    ]
    action_session = SimpleNamespace(id=uuid4())
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.find_intent_targets",
            new=AsyncMock(return_value=items),
        ),
        patch(
            "flowmate.bot.handlers.work_items.create_action_session",
            new=AsyncMock(return_value=action_session),
        ) as create_session,
        patch(
            "flowmate.bot.handlers.work_items.complete_work_item",
            new=AsyncMock(),
        ) as complete,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await execute_management_intent(
            message,
            update,
            cast(AsyncSession, session),
            make_intent(),
            high_confidence_threshold=0.8,
            action_ttl_minutes=30,
            app_timezone=ZoneInfo("UTC"),
        )

    create_session.assert_awaited_once()
    complete.assert_not_awaited()
    cast(AsyncMock, session.commit).assert_awaited_once()
    answer_call = answer.await_args
    assert answer_call is not None
    assert answer_call.args[0].startswith("Выберите запись:\n\n")
    assert "[follow-up] Follow-up 0" in answer_call.args[0]
    assert "Люди:" in answer_call.args[0]
    assert len(answer_call.kwargs["reply_markup"].inline_keyboard) == 3
    assert answer_call.kwargs["reply_markup"].inline_keyboard[-1][0].text == "Отмена"


@pytest.mark.asyncio
async def test_repeated_input_callback_does_not_send_second_force_reply() -> None:
    message = make_message("callback source")
    session = MagicMock(spec=AsyncSession)
    session.rollback = AsyncMock()
    action_session = SimpleNamespace(prompt_message_id=501)
    with (
        patch(
            "flowmate.bot.handlers.work_items.create_action_session",
            new=AsyncMock(return_value=action_session),
        ) as create_session,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await start_input_session(
            message,
            cast(AsyncSession, session),
            user_id=uuid4(),
            item_id=uuid4(),
            action=WorkItemAction.ADD_NOTE,
            prompt="Введите текст заметки.",
            ttl_minutes=30,
            telegram_update_id=9003,
        )

    create_session.assert_awaited_once()
    cast(AsyncMock, session.rollback).assert_awaited_once()
    answer.assert_awaited_once_with("Запрос уже обработан.")


@pytest.mark.asyncio
async def test_selection_can_be_cancelled_and_repeated_callback_expires() -> None:
    message = make_message("selection")
    session_id = uuid4()
    callback = CallbackQuery(
        id="selection-callback",
        from_user=cast(User, message.from_user),
        chat_instance="test",
        message=message,
        data=f"wis:{session_id}:x",
    )
    update = Update(update_id=9200, callback_query=callback)
    user = SimpleNamespace(id=uuid4())
    action_session = SimpleNamespace(
        id=session_id,
        action=WorkItemAction.SELECT_RECORD,
    )
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_action_session_for_user",
            new=AsyncMock(side_effect=[action_session, None]),
        ),
        patch(
            "flowmate.bot.handlers.work_items.finish_action_session",
            new=AsyncMock(),
        ) as finish,
        patch.object(Message, "edit_text", new_callable=AsyncMock) as edit,
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
    ):
        await work_item_selection_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
        )
        await work_item_selection_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
        )

    finish.assert_awaited_once_with(session, action_session, status="cancelled")
    edit.assert_awaited_once_with("Выбор отменён.", parse_mode=None)
    assert answer.await_args_list[0].args == ("Выбор отменён.",)
    assert answer.await_args_list[1].args == ("Срок выбора истёк.",)
    assert answer.await_args_list[1].kwargs == {"show_alert": True}


@pytest.mark.asyncio
async def test_selection_preserves_and_applies_intended_action() -> None:
    message = make_message("selection")
    session_id = uuid4()
    item = make_details("follow_up").item
    intent = make_intent()
    callback = CallbackQuery(
        id="selection-action",
        from_user=cast(User, message.from_user),
        chat_instance="test",
        message=message,
        data=f"wis:{session_id}:0",
    )
    update = Update(update_id=9201, callback_query=callback)
    user = SimpleNamespace(id=item.user_id)
    action_session = SimpleNamespace(
        id=session_id,
        action=WorkItemAction.SELECT_RECORD,
        context={
            "candidate_ids": [str(item.id)],
            "intent": intent.model_dump(mode="json"),
        },
    )
    session = MagicMock(spec=AsyncSession)
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_action_session_for_user",
            new=AsyncMock(return_value=action_session),
        ),
        patch(
            "flowmate.bot.handlers.work_items.get_work_item",
            new=AsyncMock(return_value=item),
        ),
        patch(
            "flowmate.bot.handlers.work_items.finish_action_session",
            new=AsyncMock(),
        ) as finish,
        patch(
            "flowmate.bot.handlers.work_items.apply_management_intent",
            new=AsyncMock(),
        ) as apply_intent,
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
    ):
        await work_item_selection_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
        )

    finish.assert_awaited_once_with(session, action_session)
    apply_intent.assert_awaited_once()
    apply_call = apply_intent.await_args
    assert apply_call is not None
    assert apply_call.kwargs["item"] == item
    assert apply_call.kwargs["intent"] == intent
    answer.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_waiting_received_offer_uses_revision_aware_follow_up_callback() -> None:
    message = make_message("Антон ответил")
    update = Update(update_id=9202, message=message)
    item = make_details("waiting").item
    intent = ManagementIntent(
        action=ManagementAction.WAITING_RECEIVED,
        target_type=DraftItemType.WAITING,
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
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.mark_waiting_received",
            new=AsyncMock(),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await apply_management_intent(
            message,
            update,
            cast(AsyncSession, session),
            user_id=item.user_id,
            telegram_user_id=123,
            item=item,
            intent=intent,
            action_ttl_minutes=30,
            app_timezone=ZoneInfo("UTC"),
        )

    assert answer.await_count == 2
    follow_up_markup = answer.await_args_list[1].kwargs["reply_markup"]
    callback_data = follow_up_markup.inline_keyboard[0][0].callback_data
    parsed = parse_work_item_callback(callback_data)
    assert parsed is not None
    assert parsed[0] == "f"
    assert parsed[1] == item.id
    assert parsed[2] == encode_revision(int(item.updated_at.timestamp() * 1_000_000))
    session.refresh.assert_awaited_once_with(item, attribute_names=["updated_at"])


@pytest.mark.asyncio
async def test_list_command_returns_safe_database_error() -> None:
    message = make_message("/today")
    session = MagicMock(spec=AsyncSession)
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.navigation.get_user_by_telegram_id",
            new=AsyncMock(side_effect=SQLAlchemyError("private database detail")),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await today_command(message, cast(AsyncSession, session), ZoneInfo("UTC"))

    cast(AsyncMock, session.rollback).assert_awaited_once()
    answer.assert_awaited_once_with("Не удалось загрузить список. Попробуйте позже.")
