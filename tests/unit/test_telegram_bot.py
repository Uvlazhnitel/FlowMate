# ruff: noqa: ASYNC109, ASYNC240, RUF001
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    TelegramObject,
    Update,
    User,
    Voice,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowmate.ai.schemas import DraftItemType, DraftSource
from flowmate.ai.service import DraftParsingService
from flowmate.bot.app import create_dispatcher, run_bot
from flowmate.bot.handlers.commands import (
    help_command,
    status_command,
    unsupported_message,
)
from flowmate.bot.handlers.drafts import (
    DRAFT_ANALYZING_MESSAGE,
    DRAFT_CONTROL_MESSAGE,
    parse_callback_data,
)
from flowmate.bot.handlers.notes import (
    NOTE_ALREADY_SAVED_MESSAGE,
    NOTE_SAVE_FAILED_MESSAGE,
    NOTE_SAVED_MESSAGE,
    NoteSaveOutcome,
    NoteSaveStatus,
)
from flowmate.bot.handlers.voice import (
    OVERSIZED_MESSAGE,
    PROCESSING_MESSAGE,
    SPEECH_UNAVAILABLE_MESSAGE,
    TRANSCRIPTION_FAILED_MESSAGE,
    split_transcription,
    voice_message,
)
from flowmate.bot.middleware import AllowedUserMiddleware, DatabaseSessionMiddleware
from flowmate.core.config import Settings
from flowmate.db.models import DraftSession
from flowmate.speech.errors import SpeechProviderError, SpeechTimeoutError
from flowmate.speech.service import TranscriptionService
from flowmate.speech.temp_files import TemporaryAudioFileService
from tests.ai_factories import make_analysis_result, make_draft_item, make_parse_result


def make_message(
    user_id: int,
    *,
    text: str | None = "/start",
    voice: Voice | None = None,
) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text,
        voice=voice,
    )


def make_voice_message(user_id: int, *, file_size: int | None = 5) -> Message:
    return make_message(
        user_id,
        text=None,
        voice=Voice(
            file_id="voice-file-id",
            file_unique_id="voice-file-unique-id",
            duration=2,
            mime_type="audio/ogg",
            file_size=file_size,
        ),
    )


def make_update(message: Message, update_id: int = 1001) -> Update:
    return Update(update_id=update_id, message=message)


def make_callback(user_id: int, message: Message | None = None) -> CallbackQuery:
    return CallbackQuery(
        id="callback-id",
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        chat_instance="chat-instance",
        message=message,
        data="draft:cancel",
    )


def make_mock_session() -> AsyncSession:
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return cast(AsyncSession, session)


class FakeSpeechProvider:
    def __init__(
        self,
        result: str = "Распознанный текст",
        *,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.audio_path: Path | None = None
        self.calls = 0

    async def transcribe(self, audio_path: Path) -> str:
        self.calls += 1
        self.audio_path = audio_path
        if self.error is not None:
            raise self.error
        return self.result

    async def close(self) -> None:
        return None


def make_transcription_service(
    provider: FakeSpeechProvider,
    *,
    max_file_size_bytes: int = 100,
) -> TranscriptionService:
    return TranscriptionService(
        provider,
        TemporaryAudioFileService(),
        timeout_seconds=1,
        max_file_size_bytes=max_file_size_bytes,
    )


def make_mock_bot(audio: bytes = b"audio") -> Bot:
    bot = MagicMock()

    async def download(
        file: object,
        destination: Path,
        timeout: int,
    ) -> None:
        assert isinstance(file, Voice)
        assert timeout == 1
        destination.write_bytes(audio)

    bot.download = AsyncMock(side_effect=download)
    return cast(Bot, bot)


async def invoke_voice(
    message: Message,
    bot: Bot,
    service: TranscriptionService | None,
    *,
    existing_note: object | None = None,
    save_result: NoteSaveStatus = "created",
    session: AsyncSession | None = None,
    draft_service: DraftParsingService | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    db_session = session or make_mock_session()
    get_note = AsyncMock(return_value=existing_note)
    draft = (
        DraftSession(
            id=uuid4(),
            user_id=uuid4(),
            source_note_id=uuid4(),
            status="parsing",
            expires_at=datetime.now(UTC),
        )
        if draft_service is not None and save_result == "created"
        else None
    )
    save_note = AsyncMock(return_value=NoteSaveOutcome(save_result, draft=draft))
    with (
        patch(
            "flowmate.bot.handlers.voice.get_note_by_telegram_update_id",
            new=get_note,
        ),
        patch(
            "flowmate.bot.handlers.voice.save_note_for_message",
            new=save_note,
        ),
    ):
        await voice_message(
            message,
            bot,
            make_update(message),
            db_session,
            service,
            draft_service,
        )
    return get_note, save_note


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
async def test_callback_allowlist_allows_owner_and_rejects_unknown_user(
    caplog: pytest.LogCaptureFixture,
) -> None:
    middleware = AllowedUserMiddleware(frozenset({123}))
    handler = AsyncMock(return_value="handled")

    allowed = await middleware(handler, make_callback(123), {})

    assert allowed == "handled"
    handler.assert_awaited_once()

    handler.reset_mock()
    denied_callback = make_callback(456)
    with (
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
        caplog.at_level(logging.WARNING, logger="flowmate.bot.middleware"),
    ):
        denied = await middleware(handler, denied_callback, {})

    assert denied is None
    handler.assert_not_awaited()
    answer.assert_awaited_once_with(
        "У вас нет доступа к этому действию.",
        show_alert=True,
    )
    assert "unauthorized_telegram_user user_id=456" in caplog.text


def test_stale_cancel_callback_is_rejected() -> None:
    assert parse_callback_data("draft:cancel") is None


@pytest.mark.asyncio
async def test_help_response() -> None:
    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await help_command(make_message(123, text="/help"))

    answer.assert_awaited_once_with(
        "Доступные команды: /start, /menu, /help, /status, /notes, /search. "
        "Записи: /today, /tasks, /followups, /waiting, /questions, "
        "/topics, /people. Черновики: /draft, /cancel. "
        "Напоминания: /reminders, /quiet, /snooze. "
        "Отправьте текст или голосовое сообщение, чтобы сохранить заметку."
    )


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
        "Отправьте текст, голосовое сообщение или используйте /help."
    )


@pytest.mark.asyncio
async def test_voice_message_is_downloaded_transcribed_and_deleted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = FakeSpeechProvider()
    service = make_transcription_service(provider)
    bot = make_mock_bot()
    message = make_voice_message(123)

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        _, save_note = await invoke_voice(message, bot, service)

    assert [call.args[0] for call in answer.await_args_list] == [
        PROCESSING_MESSAGE,
        "Распознанный текст",
        NOTE_SAVED_MESSAGE,
    ]
    assert answer.await_args_list[1].kwargs == {"parse_mode": None}
    assert provider.audio_path is not None
    assert not provider.audio_path.exists()
    cast(AsyncMock, bot.download).assert_awaited_once()
    save_note.assert_awaited_once()
    assert "Распознанный текст" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    [
        FakeSpeechProvider(error=SpeechProviderError("private response")),
        FakeSpeechProvider(error=SpeechTimeoutError("private timeout")),
        FakeSpeechProvider(result="   "),
    ],
)
async def test_voice_provider_failure_is_safe_and_deletes_file(
    provider: FakeSpeechProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service = make_transcription_service(provider)
    message = make_voice_message(123)

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        _, save_note = await invoke_voice(message, make_mock_bot(), service)

    assert [call.args[0] for call in answer.await_args_list] == [
        PROCESSING_MESSAGE,
        TRANSCRIPTION_FAILED_MESSAGE,
    ]
    assert provider.audio_path is not None
    assert not provider.audio_path.exists()
    save_note.assert_not_awaited()
    assert "private response" not in caplog.text
    assert "private timeout" not in caplog.text


@pytest.mark.asyncio
async def test_temporary_file_is_deleted_before_database_save_failure() -> None:
    provider = FakeSpeechProvider()
    service = make_transcription_service(provider)
    message = make_voice_message(123)

    with (
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await invoke_voice(
            message,
            make_mock_bot(),
            service,
            save_result="failed",
        )

    assert provider.audio_path is not None
    assert not provider.audio_path.exists()
    assert [call.args[0] for call in answer.await_args_list] == [
        PROCESSING_MESSAGE,
        NOTE_SAVE_FAILED_MESSAGE,
    ]


@pytest.mark.asyncio
async def test_voice_post_download_size_check_rejects_audio() -> None:
    provider = FakeSpeechProvider()
    service = make_transcription_service(provider, max_file_size_bytes=5)
    message = make_voice_message(123, file_size=None)

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        _, save_note = await invoke_voice(
            message,
            make_mock_bot(b"oversized"),
            service,
        )

    assert [call.args[0] for call in answer.await_args_list] == [
        PROCESSING_MESSAGE,
        OVERSIZED_MESSAGE,
    ]
    assert provider.calls == 0
    save_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_reported_oversized_voice_is_rejected_without_download() -> None:
    provider = FakeSpeechProvider()
    service = make_transcription_service(provider, max_file_size_bytes=5)
    bot = make_mock_bot()
    message = make_voice_message(123, file_size=6)

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        _, save_note = await invoke_voice(message, bot, service)

    answer.assert_awaited_once_with(OVERSIZED_MESSAGE)
    cast(AsyncMock, bot.download).assert_not_awaited()
    assert provider.calls == 0
    save_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_without_speech_configuration_gets_safe_response() -> None:
    bot = make_mock_bot()
    message = make_voice_message(123)

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        _, save_note = await invoke_voice(message, bot, None)

    answer.assert_awaited_once_with(SPEECH_UNAVAILABLE_MESSAGE)
    cast(AsyncMock, bot.download).assert_not_awaited()
    save_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_voice_skips_download_and_transcription() -> None:
    provider = FakeSpeechProvider()
    bot = make_mock_bot()
    message = make_voice_message(123)

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        _, save_note = await invoke_voice(
            message,
            bot,
            make_transcription_service(provider),
            existing_note=object(),
        )

    answer.assert_awaited_once_with(NOTE_ALREADY_SAVED_MESSAGE)
    cast(AsyncMock, bot.download).assert_not_awaited()
    assert provider.calls == 0
    save_note.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_voice_note_is_parsed_after_transcription() -> None:
    provider = FakeSpeechProvider()
    message = make_voice_message(123)
    result = make_analysis_result(
        make_parse_result(
            [make_draft_item(type=DraftItemType.NOTE, title="Voice note")],
            overall_intent=DraftItemType.NOTE,
            confidence=1.0,
        )
    )
    draft_service = MagicMock(spec=DraftParsingService)
    draft_service.parse = AsyncMock(return_value=result)

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await invoke_voice(
            message,
            make_mock_bot(),
            make_transcription_service(provider),
            draft_service=cast(DraftParsingService, draft_service),
        )

    cast(AsyncMock, draft_service.parse).assert_awaited_once_with(
        "Распознанный текст",
        source=DraftSource.VOICE,
    )
    assert [call.args[0] for call in answer.await_args_list[:3]] == [
        PROCESSING_MESSAGE,
        "Распознанный текст",
        DRAFT_ANALYZING_MESSAGE,
    ]
    assert "[заметка] Voice note" in answer.await_args_list[3].args[0]
    assert answer.await_args_list[3].kwargs == {"parse_mode": None}
    assert answer.await_args_list[4].args[0] == DRAFT_CONTROL_MESSAGE


@pytest.mark.asyncio
async def test_long_transcription_is_returned_completely_in_plain_chunks() -> None:
    transcription = "word " * 1800
    provider = FakeSpeechProvider(transcription)
    message = make_voice_message(123)

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await invoke_voice(
            message,
            make_mock_bot(),
            make_transcription_service(provider),
        )

    chunks = [call.args[0] for call in answer.await_args_list[1:-1]]
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert "".join(chunks) == transcription.strip()
    assert all(
        call.kwargs == {"parse_mode": None} for call in answer.await_args_list[1:-1]
    )
    assert answer.await_args_list[-1].args[0] == NOTE_SAVED_MESSAGE


@pytest.mark.asyncio
async def test_temporary_file_is_deleted_before_result_delivery_failure() -> None:
    provider = FakeSpeechProvider()
    answer = AsyncMock(side_effect=[None, RuntimeError("send failed")])
    message = make_voice_message(123)

    with (
        patch.object(Message, "answer", new=answer),
        pytest.raises(RuntimeError, match="send failed"),
    ):
        await invoke_voice(
            message,
            make_mock_bot(),
            make_transcription_service(provider),
        )

    assert provider.audio_path is not None
    assert not provider.audio_path.exists()


def test_split_transcription_handles_text_without_safe_boundaries() -> None:
    text = "x" * 8001

    chunks = split_transcription(text)

    assert [len(chunk) for chunk in chunks] == [4000, 4000, 1]
    assert "".join(chunks) == text


@pytest.mark.asyncio
async def test_unauthorized_voice_is_blocked_before_handler() -> None:
    middleware = AllowedUserMiddleware(frozenset({123}))
    handler = AsyncMock()

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await middleware(handler, make_voice_message(456), {})

    handler.assert_not_awaited()
    answer.assert_awaited_once_with("У вас нет доступа к этому боту.")


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
        speech_provider="openai",
        ai_provider="openai",
        openai_api_key="private-ai-key",
        ai_model="configured-model",
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
    ai_provider = MagicMock()
    ai_provider.close = AsyncMock()

    with (
        patch("flowmate.bot.app.create_engine", return_value=engine),
        patch("flowmate.bot.app.create_session_factory", return_value=session_factory),
        patch("flowmate.bot.app.create_dispatcher", return_value=dispatcher),
        patch("flowmate.bot.app.create_ai_provider", return_value=ai_provider),
        patch("flowmate.bot.app.Bot", return_value=bot_context),
    ):
        await run_bot(settings)

    dispatcher.start_polling.assert_awaited_once_with(
        bot,
        allowed_updates=["message"],
        close_bot_session=False,
    )
    engine.dispose.assert_awaited_once()
    ai_provider.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_incomplete_ai_configuration_does_not_block_bot_polling() -> None:
    settings = Settings(
        _env_file=None,
        telegram_allowed_user_ids=frozenset({123}),
        telegram_bot_token="123456:test-token",
        ai_provider="openai",
    )
    engine = MagicMock()
    engine.dispose = AsyncMock()
    dispatcher = MagicMock()
    dispatcher.resolve_used_update_types.return_value = ["message"]
    dispatcher.start_polling = AsyncMock()
    bot = MagicMock()
    bot_context = MagicMock()
    bot_context.__aenter__ = AsyncMock(return_value=bot)
    bot_context.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("flowmate.bot.app.create_engine", return_value=engine),
        patch("flowmate.bot.app.create_session_factory", return_value=MagicMock()),
        patch("flowmate.bot.app.create_dispatcher", return_value=dispatcher),
        patch("flowmate.bot.app.Bot", return_value=bot_context),
    ):
        await run_bot(settings)

    dispatcher.start_polling.assert_awaited_once()
    engine.dispose.assert_awaited_once()


def test_dispatcher_registers_message_and_callback_updates() -> None:
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

    assert dispatcher.resolve_used_update_types() == ["callback_query", "message"]
