# ruff: noqa: ASYNC109, ASYNC240, RUF001
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.types import Chat, InlineKeyboardMarkup, Message, Update, User, Voice
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import DraftSource
from flowmate.ai.service import DraftParsingService
from flowmate.bot.handlers.meeting_capture import (
    capture_keyboard,
    meeting_text_capture,
    meeting_voice_capture,
)
from flowmate.bot.handlers.meeting_review import (
    format_review_summary,
    meeting_review_reply,
    review_keyboard,
)
from flowmate.bot.handlers.meetings import setup_keyboard, type_keyboard
from flowmate.db.models import DraftSession, Meeting, MeetingReview, Note
from flowmate.meetings.enums import MeetingType
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.speech.service import TranscriptionService
from flowmate.speech.temp_files import TemporaryAudioFileService


def callback_values(keyboard: InlineKeyboardMarkup) -> set[str]:
    rows = keyboard.inline_keyboard
    return {
        button.callback_data
        for row in rows
        for button in row
        if button.callback_data is not None
    }


def make_message(
    *, text: str | None = "Capture", voice: Voice | None = None
) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=123, type="private"),
        from_user=User(id=123, is_bot=False, first_name="Test"),
        text=text,
        voice=voice,
    )


def make_capture() -> tuple[Meeting, DraftSession]:
    user_id = uuid4()
    meeting_id = uuid4()
    meeting = Meeting(
        id=meeting_id,
        user_id=user_id,
        title="Weekly",
        type="team",
        status="active",
    )
    capture = DraftSession(
        id=uuid4(),
        user_id=user_id,
        source_note_id=uuid4(),
        meeting_id=meeting_id,
        capture_sequence=4,
        capture_review_status="pending",
        capture_context={
            "meeting_id": str(meeting_id),
            "meeting_type": "team",
            "timezone": "Europe/Riga",
            "captured_at": datetime.now(UTC).isoformat(),
            "participants": [],
            "topics": [],
            "primary_topic_id": None,
        },
        status="parsing",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )
    return meeting, capture


def defaults() -> NotificationDefaults:
    return NotificationDefaults(
        timezone="Europe/Riga",
        morning_digest_time=time(8),
        evening_digest_time=time(18),
        quiet_hours_start=time(22),
        quiet_hours_end=time(7),
        snooze_minutes=60,
    )


def test_meeting_type_keyboard_exposes_supported_types() -> None:
    callbacks = callback_values(type_keyboard())

    assert {
        f"mt:type:{meeting_type.value}" for meeting_type in MeetingType
    } <= callbacks
    assert "mt:abort" in callbacks


def test_meeting_setup_keyboard_contains_only_compact_actions() -> None:
    callbacks = callback_values(setup_keyboard())

    assert callbacks == {
        "mt:title",
        "mt:people:0",
        "mt:topics:0",
        "mt:review",
        "mt:abort",
    }
    assert all(len(value.encode()) <= 64 for value in callbacks)


def test_capture_keyboard_uses_compact_capture_id() -> None:
    capture_id = uuid4()

    assert callback_values(capture_keyboard(capture_id)) == {f"mc:undo:{capture_id}"}


def test_meeting_review_keyboard_and_summary_are_compact() -> None:
    meeting_id = uuid4()
    callbacks = callback_values(review_keyboard(meeting_id))

    assert callbacks == {
        f"mr:clarify:{meeting_id}",
        f"mr:show:{meeting_id}",
        f"mr:confirm:{meeting_id}",
        f"mr:inbox:{meeting_id}",
    }
    assert all(len(value.encode()) <= 64 for value in callbacks)
    summary = format_review_summary(
        {
            "counts": {"task": 3, "decision": 2},
            "items": [{"status": "clarification_required"}],
        }
    )
    assert "— 3 задачи" in summary
    assert "— 2 решения" in summary
    assert "Требуют уточнения: 1" in summary


@pytest.mark.asyncio
async def test_text_capture_acknowledges_before_deferred_analysis() -> None:
    meeting, capture = make_capture()
    message = make_message()
    order: list[str] = []
    save = AsyncMock(return_value=(capture, True))
    analyze = AsyncMock(side_effect=lambda **_: order.append("analyze"))

    async def answer(*_: object, **__: object) -> None:
        order.append("ack")

    with (
        patch.object(Message, "answer", new=AsyncMock(side_effect=answer)) as reply,
        patch("flowmate.bot.handlers.meeting_capture._save_capture", new=save),
        patch("flowmate.bot.handlers.meeting_capture._analyze_capture", new=analyze),
    ):
        await meeting_text_capture(
            message,
            Update(update_id=7001, message=message),
            cast(AsyncSession, MagicMock(spec=AsyncSession)),
            meeting,
            meeting.user_id,
            defaults(),
        )

    assert order == ["ack", "analyze"]
    reply_call = reply.await_args
    analyze_call = analyze.await_args
    assert reply_call is not None
    assert analyze_call is not None
    assert reply_call.args[0] == "✅ Записал. Пункт №4."
    assert reply_call.kwargs["reply_markup"] == capture_keyboard(capture.id)
    assert analyze_call.kwargs["source"] is DraftSource.TEXT


@pytest.mark.asyncio
async def test_empty_meeting_text_requests_a_meaningful_retry() -> None:
    meeting, _ = make_capture()
    message = make_message(text="   ")

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await meeting_text_capture(
            message,
            Update(update_id=7002, message=message),
            cast(AsyncSession, MagicMock(spec=AsyncSession)),
            meeting,
            meeting.user_id,
            defaults(),
        )

    answer.assert_awaited_once_with(
        "Не удалось сохранить пустой пункт. Повторите сообщение."
    )


class CapturingSpeechProvider:
    def __init__(self) -> None:
        self.audio_path: Path | None = None

    async def transcribe(self, audio_path: Path) -> str:
        self.audio_path = audio_path
        return "Голосовой пункт"

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_voice_capture_deletes_audio_and_defers_analysis() -> None:
    meeting, capture = make_capture()
    message = make_message(
        text=None,
        voice=Voice(
            file_id="voice",
            file_unique_id="voice-unique",
            duration=2,
            file_size=5,
        ),
    )
    provider = CapturingSpeechProvider()
    transcription = TranscriptionService(
        provider,
        TemporaryAudioFileService(),
        timeout_seconds=1,
        max_file_size_bytes=100,
    )
    bot = MagicMock(spec=Bot)

    async def download(_: object, destination: Path, timeout: int) -> None:
        assert timeout == 1
        destination.write_bytes(b"audio")

    bot.download = AsyncMock(side_effect=download)
    session = MagicMock(spec=AsyncSession)
    session.rollback = AsyncMock()
    analyze = AsyncMock()
    with (
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
        patch(
            "flowmate.bot.handlers.meeting_capture.get_note_by_telegram_update_id",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.meeting_capture._save_capture",
            new=AsyncMock(return_value=(capture, True)),
        ),
        patch("flowmate.bot.handlers.meeting_capture._analyze_capture", new=analyze),
    ):
        await meeting_voice_capture(
            message,
            cast(Bot, bot),
            Update(update_id=7003, message=message),
            cast(AsyncSession, session),
            meeting,
            meeting.user_id,
            defaults(),
            transcription,
        )

    assert provider.audio_path is not None
    assert not provider.audio_path.exists()
    assert [call.args[0] for call in answer.await_args_list] == [
        "Обрабатываю голосовое сообщение.",
        "✅ Записал. Пункт №4.",
    ]
    analyze_call = analyze.await_args
    assert analyze_call is not None
    assert analyze_call.kwargs["content"] == "Голосовой пункт"
    assert analyze_call.kwargs["source"] is DraftSource.VOICE


@pytest.mark.asyncio
async def test_duplicate_voice_capture_skips_download() -> None:
    meeting, capture = make_capture()
    message = make_message(
        text=None,
        voice=Voice(
            file_id="voice",
            file_unique_id="voice-unique",
            duration=2,
            file_size=5,
        ),
    )
    note = Note(
        id=capture.source_note_id,
        user_id=meeting.user_id,
        content="Existing",
        source="voice",
        telegram_update_id=7004,
    )
    bot = MagicMock(spec=Bot)
    bot.download = AsyncMock()
    session = MagicMock(spec=AsyncSession)
    session.rollback = AsyncMock()
    with (
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
        patch(
            "flowmate.bot.handlers.meeting_capture.get_note_by_telegram_update_id",
            new=AsyncMock(return_value=note),
        ),
        patch(
            "flowmate.bot.handlers.meeting_capture.get_capture_by_note",
            new=AsyncMock(return_value=capture),
        ),
    ):
        await meeting_voice_capture(
            message,
            cast(Bot, bot),
            Update(update_id=7004, message=message),
            cast(AsyncSession, session),
            meeting,
            meeting.user_id,
            defaults(),
            None,
        )

    cast(AsyncMock, bot.download).assert_not_awaited()
    answer.assert_awaited_once()
    answer_call = answer.await_args
    assert answer_call is not None
    assert answer_call.args[0] == "✅ Записал. Пункт №4."


@pytest.mark.asyncio
async def test_voice_meeting_review_clarification_uses_structured_service() -> None:
    meeting, _ = make_capture()
    message = make_message(
        text=None,
        voice=Voice(
            file_id="review-voice",
            file_unique_id="review-voice-unique",
            duration=2,
            file_size=5,
        ),
    )
    provider = CapturingSpeechProvider()
    transcription = TranscriptionService(
        provider,
        TemporaryAudioFileService(),
        timeout_seconds=1,
        max_file_size_bytes=100,
    )
    review = MeetingReview(
        id=uuid4(),
        user_id=meeting.user_id,
        meeting_id=meeting.id,
        status="review_required",
        current_item_id=uuid4(),
        current_question="Какой срок?",
    )
    bot = MagicMock(spec=Bot)

    async def download(_: object, destination: Path, timeout: int) -> None:
        destination.write_bytes(b"audio")

    bot.download = AsyncMock(side_effect=download)
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    parsing_service = cast(DraftParsingService, MagicMock())
    answer_review = AsyncMock(return_value=review)
    with (
        patch.object(Message, "answer", new_callable=AsyncMock),
        patch(
            "flowmate.bot.handlers.meeting_review.answer_review_item",
            new=answer_review,
        ),
        patch(
            "flowmate.bot.handlers.meeting_review._next_question",
            new=AsyncMock(return_value=False),
        ),
    ):
        await meeting_review_reply(
            message,
            cast(Bot, bot),
            Update(update_id=7005, message=message),
            cast(AsyncSession, session),
            review,
            transcription,
            parsing_service,
        )

    assert provider.audio_path is not None
    assert not provider.audio_path.exists()
    call = answer_review.await_args
    assert call is not None
    assert call.args[4] == "Голосовой пункт"
    assert call.kwargs["answer_source"] is DraftSource.VOICE
    assert call.kwargs["parsing_service"] is parsing_service
    session.commit.assert_awaited_once()
