from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Chat, Message, User

from flowmate.bot.handlers.navigation.menu import menu_command
from flowmate.bot.menu import (
    CANCEL_BUTTON,
    FOLLOW_UPS_BUTTON,
    QUESTIONS_BUTTON,
    RECORD_BUTTON,
    SEARCH_BUTTON,
    SETTINGS_BUTTON,
    TASKS_BUTTON,
    TODAY_BUTTON,
    TOMORROW_BUTTON,
    WAITING_BUTTON,
    WORKSPACE_BUTTON,
    main_menu_keyboard,
)


def make_message() -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=100, type="private"),
        from_user=User(id=100, is_bot=False, first_name="Test"),
        text="/menu",
    )


def test_main_menu_has_persistent_layout_and_workspace_button() -> None:
    keyboard = main_menu_keyboard()

    assert keyboard.is_persistent is True
    assert [[button.text for button in row] for row in keyboard.keyboard] == [
        [RECORD_BUTTON],
        [TODAY_BUTTON, TOMORROW_BUTTON],
        [TASKS_BUTTON, FOLLOW_UPS_BUTTON],
        [WAITING_BUTTON, QUESTIONS_BUTTON],
        [SEARCH_BUTTON, SETTINGS_BUTTON],
        [WORKSPACE_BUTTON, CANCEL_BUTTON],
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
