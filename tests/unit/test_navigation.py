from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import Chat, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import SearchIntent, SearchWorkItemType
from flowmate.bot.handlers.commands import cancel_command
from flowmate.bot.handlers.navigation import (
    FOLLOW_UPS_BUTTON,
    PEOPLE_BUTTON,
    QUESTIONS_BUTTON,
    RECORD_BUTTON,
    SEARCH_BUTTON,
    SETTINGS_BUTTON,
    TASKS_BUTTON,
    TODAY_BUTTON,
    TOPICS_BUTTON,
    WAITING_BUTTON,
    NavigationPage,
    execute_search_intent,
    list_keyboard,
    main_menu_keyboard,
    menu_command,
    normalize_display_text,
    parse_list_callback,
    parse_search_callback,
    parse_search_expression,
    send_navigation_page,
)
from flowmate.db.models import WorkItem


def make_message() -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=100, type="private"),
        from_user=User(id=100, is_bot=False, first_name="Test"),
        text="/menu",
    )


def test_main_menu_has_persistent_two_column_layout() -> None:
    keyboard = main_menu_keyboard()

    assert keyboard.is_persistent is True
    assert [[button.text for button in row] for row in keyboard.keyboard] == [
        [RECORD_BUTTON, TODAY_BUTTON],
        [TASKS_BUTTON, FOLLOW_UPS_BUTTON],
        [WAITING_BUTTON, QUESTIONS_BUTTON],
        [PEOPLE_BUTTON, TOPICS_BUTTON],
        [SEARCH_BUTTON, SETTINGS_BUTTON],
    ]


@pytest.mark.asyncio
async def test_menu_command_shows_main_keyboard() -> None:
    message = make_message()
    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await menu_command(message)

    call = answer.await_args
    assert call is not None
    kwargs = call.kwargs
    assert call.args == ("Главное меню FlowMate.",)
    assert kwargs["parse_mode"] is None
    assert kwargs["reply_markup"].is_persistent is True


def test_list_callback_parsing_rejects_invalid_pages_and_views() -> None:
    assert parse_list_callback("ls:t:0") == ("t", 0)
    assert parse_list_callback("ls:t:999") == ("t", 999)
    assert parse_list_callback("ls:t:-1") is None
    assert parse_list_callback("ls:s:0") is None
    assert parse_list_callback("ls:private:0") is None
    assert parse_list_callback("ls:t:not-a-page") is None


def test_search_callback_uses_only_session_id_and_page() -> None:
    session_id = uuid4()

    assert parse_search_callback(f"lq:{session_id}:2") == (session_id, 2)
    assert parse_search_callback(f"lq:{session_id}:-1") is None
    assert parse_search_callback("lq:not-a-uuid:0") is None


def test_search_expression_parses_filters_and_quoted_values() -> None:
    filters = parse_search_expression(
        'release person:"Антон Иванов" topic:Testing type:follow-up '
        "status:active,waiting from:2026-07-01 to:2026-07-31",
        ZoneInfo("Europe/Riga"),
    )

    assert filters.text_query == "release"
    assert filters.person_query == "Антон Иванов"
    assert filters.topic_query == "Testing"
    assert filters.item_types == ("follow_up",)
    assert filters.statuses == ("active", "waiting")
    assert filters.due_from is not None and filters.due_from.hour == 0
    assert (
        filters.due_to is not None and filters.due_to.date().isoformat() == "2026-08-01"
    )


@pytest.mark.parametrize(
    "query",
    [
        "type:unknown",
        "status:all status:done",
        "from:31.07.2026",
        "from:2026-08-01 to:2026-07-01",
        "overdue from:2026-07-01",
        'person:"unfinished',
    ],
)
def test_search_expression_rejects_invalid_filters(query: str) -> None:
    with pytest.raises(ValueError):
        parse_search_expression(query, ZoneInfo("UTC"))


def test_page_keyboard_handles_first_middle_and_last_page() -> None:
    item_id = uuid4()
    first = list_keyboard(view="t", page=0, has_next=True, item_ids=[item_id])
    middle = list_keyboard(view="t", page=1, has_next=True, item_ids=[])
    last = list_keyboard(view="t", page=2, has_next=False, item_ids=[])

    assert [button.text for button in first.inline_keyboard[-2]] == ["Вперёд"]
    assert [button.text for button in middle.inline_keyboard[-2]] == [
        "Назад",
        "Вперёд",
    ]
    assert [button.text for button in last.inline_keyboard[-2]] == ["Назад"]
    assert first.inline_keyboard[-1][0].callback_data == "nav:menu"


def test_user_text_is_normalized_and_truncated() -> None:
    value = normalize_display_text("  private\n   title  " + "x" * 100, 20)

    assert "\n" not in value
    assert len(value) <= 20
    assert value.endswith("…")


@pytest.mark.asyncio
async def test_long_navigation_page_is_split_below_telegram_limit() -> None:
    message = make_message()
    page = NavigationPage(
        text=("safe text " * 1000),
        keyboard=list_keyboard(view="t", page=0, has_next=False, item_ids=[]),
    )
    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await send_navigation_page(message, page)

    assert answer.await_count > 1
    assert all(len(call.args[0]) <= 4000 for call in answer.await_args_list)
    assert answer.await_args_list[-1].kwargs["reply_markup"] == page.keyboard
    assert all(call.kwargs["parse_mode"] is None for call in answer.await_args_list)


@pytest.mark.asyncio
async def test_cancel_command_cancels_active_search_action() -> None:
    message = make_message()
    session = AsyncMock(spec=AsyncSession)
    user = SimpleNamespace(id=uuid4())
    action = SimpleNamespace(id=uuid4())
    with (
        patch(
            "flowmate.bot.handlers.commands.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.commands.get_active_action_session",
            new=AsyncMock(return_value=action),
        ),
        patch(
            "flowmate.bot.handlers.commands.finish_action_session",
            new=AsyncMock(),
        ) as finish,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await cancel_command(message, cast(AsyncSession, session))

    finish.assert_awaited_once_with(
        session,
        cast(object, action),
        status="cancelled",
    )
    session.commit.assert_awaited_once()
    answer.assert_awaited_once_with("Текущее действие отменено.")


def make_search_intent(*, confidence: float = 0.95) -> SearchIntent:
    return SearchIntent(
        text_query=None,
        person_query="Антон",
        topic_query=None,
        item_types=[SearchWorkItemType.FOLLOW_UP],
        statuses=[],
        include_all_statuses=False,
        due_from=None,
        due_to=None,
        overdue=False,
        stale_contacts=False,
        ambiguities=[],
        confidence=confidence,
    )


@pytest.mark.asyncio
async def test_conversational_search_opens_one_clear_result() -> None:
    message = make_message()
    update = Update(update_id=9701, message=message)
    session = AsyncMock(spec=AsyncSession)
    user = SimpleNamespace(id=uuid4())
    action = SimpleNamespace(id=uuid4(), context={}, status="completed")
    item = WorkItem(
        id=uuid4(),
        user_id=user.id,
        type="follow_up",
        title="Позвонить Антону",
        status="inbox",
        priority="normal",
    )
    with (
        patch(
            "flowmate.bot.handlers.navigation.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.navigation.create_action_session",
            new=AsyncMock(return_value=action),
        ),
        patch(
            "flowmate.bot.handlers.navigation.finish_action_session",
            new=AsyncMock(),
        ),
        patch(
            "flowmate.bot.handlers.navigation.search_work_items",
            new=AsyncMock(return_value=[item]),
        ),
        patch(
            "flowmate.bot.handlers.navigation.send_details",
            new=AsyncMock(return_value=True),
        ) as send_details,
    ):
        await execute_search_intent(
            message,
            update,
            cast(AsyncSession, session),
            make_search_intent(),
            high_confidence_threshold=0.8,
            action_ttl_minutes=30,
            timezone=ZoneInfo("UTC"),
        )

    session.commit.assert_awaited_once()
    send_details.assert_awaited_once_with(
        message,
        session,
        user.id,
        item,
        ZoneInfo("UTC"),
    )


@pytest.mark.asyncio
async def test_low_confidence_search_does_not_query_database() -> None:
    message = make_message()
    with (
        patch(
            "flowmate.bot.handlers.navigation.search_work_items",
            new=AsyncMock(),
        ) as search,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await execute_search_intent(
            message,
            Update(update_id=9702, message=message),
            cast(AsyncSession, AsyncMock(spec=AsyncSession)),
            make_search_intent(confidence=0.4),
            high_confidence_threshold=0.8,
            action_ttl_minutes=30,
            timezone=ZoneInfo("UTC"),
        )

    search.assert_not_awaited()
    answer.assert_awaited_once_with("Уточните поисковый запрос и попробуйте ещё раз.")
