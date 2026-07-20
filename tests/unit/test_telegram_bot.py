# ruff: noqa: RUF001
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Chat, Message, TelegramObject, User
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowmate.bot.app import create_dispatcher, run_bot
from flowmate.bot.handlers.commands import (
    help_command,
    status_command,
    unsupported_message,
)
from flowmate.bot.middleware import AllowedUserMiddleware, DatabaseSessionMiddleware
from flowmate.core.config import Settings


def make_message(user_id: int, *, text: str = "/start") -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text,
    )


@pytest.mark.asyncio
async def test_allowed_user_reaches_handler() -> None:
    middleware = AllowedUserMiddleware(frozenset({123}))
    handler = AsyncMock(return_value="handled")

    result = await middleware(handler, make_message(123), {})

    assert result == "handled"
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_denied_user_gets_safe_response_and_numeric_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    middleware = AllowedUserMiddleware(frozenset({123}))
    handler = AsyncMock()
    message = make_message(456, text="private message contents")

    with (
        caplog.at_level(logging.WARNING, logger="flowmate.bot.middleware"),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        result = await middleware(handler, message, {})

    assert result is None
    handler.assert_not_awaited()
    answer.assert_awaited_once_with("У вас нет доступа к этому боту.")
    assert "unauthorized_telegram_user user_id=456" in caplog.text
    assert "private message contents" not in caplog.text


@pytest.mark.asyncio
async def test_help_response() -> None:
    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await help_command(make_message(123, text="/help"))

    answer.assert_awaited_once_with("Доступные команды: /start, /help, /status.")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ready", "expected"),
    [
        (True, "Бот работает, база данных доступна."),
        (False, "Сервис временно недоступен. Попробуйте позже."),
    ],
)
async def test_status_response(ready: bool, expected: str) -> None:
    engine = cast(AsyncEngine, MagicMock())
    with (
        patch(
            "flowmate.bot.handlers.commands.database_is_ready",
            new=AsyncMock(return_value=ready),
        ) as readiness,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await status_command(make_message(123, text="/status"), engine)

    readiness.assert_awaited_once_with(engine)
    answer.assert_awaited_once_with(expected)


@pytest.mark.asyncio
async def test_unsupported_message_response() -> None:
    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await unsupported_message(make_message(123, text="hello"))

    answer.assert_awaited_once_with(
        "Пока доступны только команды /start, /help и /status."
    )


@pytest.mark.asyncio
async def test_missing_bot_token_fails_before_engine_creation() -> None:
    settings = Settings(
        _env_file=None,
        telegram_allowed_user_ids=frozenset({123}),
        telegram_bot_token=None,
    )
    with patch("flowmate.bot.app.create_engine") as create_engine_mock:
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
            await run_bot(settings)

    create_engine_mock.assert_not_called()


@pytest.mark.asyncio
async def test_database_middleware_injects_update_scoped_resources() -> None:
    session_factory = cast(
        async_sessionmaker[AsyncSession], MagicMock(spec=async_sessionmaker)
    )
    engine = cast(AsyncEngine, MagicMock())
    session = cast(AsyncSession, MagicMock())
    middleware = DatabaseSessionMiddleware(session_factory, engine)

    @asynccontextmanager
    async def fake_session_scope(
        actual_factory: async_sessionmaker[AsyncSession],
    ) -> AsyncIterator[AsyncSession]:
        assert actual_factory is session_factory
        yield session

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        assert isinstance(event, Message)
        assert data["db_session"] is session
        assert data["db_engine"] is engine
        return "handled"

    with patch("flowmate.bot.middleware.session_scope", new=fake_session_scope):
        result = await middleware(handler, make_message(123), {})

    assert result == "handled"


@pytest.mark.asyncio
async def test_bot_polling_closes_engine_without_real_telegram_api() -> None:
    settings = Settings(
        _env_file=None,
        telegram_allowed_user_ids=frozenset({123}),
        telegram_bot_token="123456:test-token",
    )
    engine = MagicMock()
    engine.dispose = AsyncMock()
    session_factory = MagicMock()
    dispatcher = MagicMock()
    dispatcher.resolve_used_update_types.return_value = ["message"]
    dispatcher.start_polling = AsyncMock()
    bot = MagicMock()
    bot_context = MagicMock()
    bot_context.__aenter__ = AsyncMock(return_value=bot)
    bot_context.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("flowmate.bot.app.create_engine", return_value=engine),
        patch("flowmate.bot.app.create_session_factory", return_value=session_factory),
        patch("flowmate.bot.app.create_dispatcher", return_value=dispatcher),
        patch("flowmate.bot.app.Bot", return_value=bot_context),
    ):
        await run_bot(settings)

    dispatcher.start_polling.assert_awaited_once_with(
        bot,
        allowed_updates=["message"],
        close_bot_session=False,
    )
    engine.dispose.assert_awaited_once()


def test_dispatcher_registers_only_message_updates() -> None:
    settings = Settings(
        _env_file=None,
        telegram_allowed_user_ids=frozenset({123}),
        telegram_bot_token="123456:test-token",
    )
    session_factory = cast(
        async_sessionmaker[AsyncSession], MagicMock(spec=async_sessionmaker)
    )
    engine = cast(AsyncEngine, MagicMock())

    dispatcher = create_dispatcher(settings, session_factory, engine)

    assert dispatcher.resolve_used_update_types() == ["message"]
