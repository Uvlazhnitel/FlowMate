# ruff: noqa: RUF001
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.handlers.navigation.lists import (
    build_navigation_page,
    list_callback,
    send_navigation_page,
    tomorrow_command,
)
from flowmate.bot.handlers.navigation.presentation import (
    NavigationPage,
    list_keyboard,
    normalize_display_text,
    parse_list_callback,
)
from flowmate.reminders.preferences import NotificationDefaults


def make_message() -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=100, type="private"),
        from_user=User(id=100, is_bot=False, first_name="Test"),
        text="/menu",
    )


@pytest.mark.asyncio
async def test_tomorrow_navigation_uses_exact_next_local_day() -> None:
    session = AsyncMock(spec=AsyncSession)
    timezone = ZoneInfo("America/New_York")
    with patch(
        "flowmate.bot.handlers.navigation.lists.list_scheduled_items",
        new=AsyncMock(return_value=[]),
    ) as scheduled:
        page = await build_navigation_page(
            cast(AsyncSession, session),
            uuid4(),
            view="n",
            page=0,
            timezone=timezone,
        )

    assert "📆 На завтра" in page.text
    assert "На завтра записей нет." in page.text
    call = scheduled.await_args
    assert call is not None
    start = call.kwargs["start"]
    end = call.kwargs["end"]
    expected_date = datetime.now(timezone).date() + timedelta(days=1)
    assert start.astimezone(timezone).date() == expected_date
    assert end.astimezone(timezone).date() == expected_date + timedelta(days=1)


@pytest.mark.asyncio
async def test_tomorrow_command_uses_user_notification_timezone() -> None:
    message = make_message()
    session = AsyncMock(spec=AsyncSession)
    user = SimpleNamespace(id=uuid4())
    preferences = SimpleNamespace(zoneinfo=ZoneInfo("Asia/Tokyo"))
    defaults = NotificationDefaults(
        timezone="UTC",
        morning_digest_time=datetime.min.time(),
        evening_digest_time=datetime.min.time(),
        quiet_hours_start=datetime.min.time(),
        quiet_hours_end=datetime.min.time(),
        snooze_minutes=60,
    )
    navigation_page = NavigationPage(
        text="Tomorrow",
        keyboard=list_keyboard(view="n", page=0, has_next=False, item_ids=[]),
    )
    with (
        patch(
            "flowmate.bot.handlers.navigation.lists.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.navigation.lists.cancel_transient_dialogs",
            new=AsyncMock(),
        ),
        patch(
            "flowmate.bot.handlers.navigation.lists.get_effective_notification_preferences",
            new=AsyncMock(return_value=preferences),
        ),
        patch(
            "flowmate.bot.handlers.navigation.lists.build_navigation_page",
            new=AsyncMock(return_value=navigation_page),
        ) as build,
        patch(
            "flowmate.bot.handlers.navigation.lists.send_navigation_page",
            new=AsyncMock(),
        ),
    ):
        await tomorrow_command(
            message,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            defaults,
        )

    assert build.await_args is not None
    assert build.await_args.kwargs["view"] == "n"
    assert build.await_args.kwargs["timezone"] == ZoneInfo("Asia/Tokyo")


@pytest.mark.asyncio
async def test_list_callback_acknowledges_before_loading_page() -> None:
    message = make_message()
    callback = CallbackQuery(
        id="list-callback",
        from_user=cast(User, message.from_user),
        chat_instance="test",
        message=message,
        data="ls:t:1",
    )
    session = AsyncMock(spec=AsyncSession)
    user = SimpleNamespace(id=uuid4())
    preferences = SimpleNamespace(zoneinfo=ZoneInfo("Asia/Tokyo"))
    defaults = NotificationDefaults(
        timezone="UTC",
        morning_digest_time=datetime.min.time(),
        evening_digest_time=datetime.min.time(),
        quiet_hours_start=datetime.min.time(),
        quiet_hours_end=datetime.min.time(),
        snooze_minutes=60,
    )
    page = NavigationPage(
        text="Page",
        keyboard=list_keyboard(view="t", page=1, has_next=False, item_ids=[]),
    )
    events: list[str] = []

    async def acknowledge(*_: object, **__: object) -> None:
        events.append("acknowledged")

    async def build(*_: object, **__: object) -> NavigationPage:
        events.append("loaded")
        return page

    with (
        patch(
            "flowmate.bot.handlers.navigation.lists.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.navigation.lists.build_navigation_page",
            new=AsyncMock(side_effect=build),
        ) as build_page,
        patch(
            "flowmate.bot.handlers.navigation.lists.get_effective_notification_preferences",
            new=AsyncMock(return_value=preferences),
        ),
        patch(
            "flowmate.bot.handlers.navigation.lists.send_navigation_page",
            new_callable=AsyncMock,
        ) as send,
        patch.object(
            CallbackQuery,
            "answer",
            new=AsyncMock(side_effect=acknowledge),
        ) as answer,
    ):
        await list_callback(
            callback,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            defaults,
        )

    answer.assert_awaited_once_with("⏳ Открываю…")
    assert events == ["acknowledged", "loaded"]
    assert build_page.await_args is not None
    assert build_page.await_args.kwargs["timezone"] == ZoneInfo("Asia/Tokyo")
    send.assert_awaited_once_with(message, page, edit=True)


def test_list_callback_parsing_rejects_invalid_pages_and_views() -> None:
    assert parse_list_callback("ls:t:0") == ("t", 0, None)
    assert parse_list_callback("ls:t:999") == ("t", 999, None)
    assert parse_list_callback("ls:p:0") == ("p", 0, "work")
    assert parse_list_callback("ls:p:recent:2") == ("p", 2, "recent")
    assert parse_list_callback("ls:p:all:0") == ("p", 0, "all")
    assert parse_list_callback("ls:t:-1") is None
    assert parse_list_callback("ls:s:0") is None
    assert parse_list_callback("ls:t:all:0") is None
    assert parse_list_callback("ls:p:archived:0") is None
    assert parse_list_callback("ls:private:0") is None
    assert parse_list_callback("ls:t:not-a-page") is None


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


def test_people_keyboard_has_scopes_and_scoped_pagination() -> None:
    keyboard = list_keyboard(
        view="p",
        page=1,
        has_next=True,
        item_ids=[],
        people_scope="recent",
    )

    assert [button.text for button in keyboard.inline_keyboard[0]] == [
        "В работе",
        "• Недавние",
        "Все",
    ]
    assert [button.callback_data for button in keyboard.inline_keyboard[0]] == [
        "ls:p:work:0",
        "ls:p:recent:0",
        "ls:p:all:0",
    ]
    assert [button.callback_data for button in keyboard.inline_keyboard[1]] == [
        "ls:p:recent:0",
        "ls:p:recent:2",
    ]


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
