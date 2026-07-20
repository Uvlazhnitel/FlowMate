from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Update, Voice
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.handlers.notes import (
    NOTE_ALREADY_SAVED_MESSAGE,
    NOTE_SAVED_MESSAGE,
    text_note,
)
from flowmate.bot.handlers.voice import voice_message
from flowmate.db.models import Note
from flowmate.db.notes import create_note_idempotently, list_recent_notes_for_user
from flowmate.db.users import create_telegram_user
from flowmate.speech.service import TranscriptionService


def make_message(
    user_id: int,
    *,
    text: str | None = None,
    voice: Voice | None = None,
) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=TelegramUser(id=user_id, is_bot=False, first_name="Test"),
        text=text,
        voice=voice,
    )


def make_update(message: Message, update_id: int) -> Update:
    return Update(update_id=update_id, message=message)


@pytest.mark.integration
async def test_note_creation_is_idempotent_and_owned_by_user(
    database_session: AsyncSession,
) -> None:
    owner = await create_telegram_user(database_session, 300_001)
    other = await create_telegram_user(database_session, 300_002)
    first, first_created = await create_note_idempotently(
        database_session,
        user_id=owner.id,
        content="Original content",
        source="text",
        telegram_update_id=400_001,
    )
    duplicate, duplicate_created = await create_note_idempotently(
        database_session,
        user_id=other.id,
        content="Must not replace content",
        source="voice",
        telegram_update_id=400_001,
    )

    owner_notes = await list_recent_notes_for_user(database_session, owner.id)
    other_notes = await list_recent_notes_for_user(database_session, other.id)
    count = await database_session.scalar(select(func.count(Note.id)))

    assert first_created is True
    assert duplicate_created is False
    assert duplicate.id == first.id
    assert duplicate.user_id == owner.id
    assert duplicate.content == "Original content"
    assert [note.id for note in owner_notes] == [first.id]
    assert other_notes == []
    assert count == 1


@pytest.mark.integration
async def test_recent_notes_are_limited_and_newest_first(
    database_session: AsyncSession,
) -> None:
    owner = await create_telegram_user(database_session, 300_003)
    created_notes: list[Note] = []
    for index in range(12):
        note, _ = await create_note_idempotently(
            database_session,
            user_id=owner.id,
            content=f"Note {index}",
            source="text" if index % 2 == 0 else "voice",
            telegram_update_id=410_000 + index,
        )
        note.created_at = datetime(2026, 1, 1, index, tzinfo=UTC)
        created_notes.append(note)
    await database_session.flush()

    recent = await list_recent_notes_for_user(database_session, owner.id)

    assert len(recent) == 10
    assert [note.content for note in recent] == [
        f"Note {index}" for index in range(11, 1, -1)
    ]


@pytest.mark.integration
async def test_repeated_text_update_creates_one_note(
    database_session: AsyncSession,
) -> None:
    message = make_message(300_004, text="  Plain text note  ")
    update = make_update(message, 420_001)

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await text_note(message, update, database_session)
        await text_note(message, update, database_session)

    notes = list(await database_session.scalars(select(Note)))
    assert len(notes) == 1
    assert notes[0].content == "Plain text note"
    assert notes[0].source == "text"
    assert [call.args[0] for call in answer.await_args_list] == [
        NOTE_SAVED_MESSAGE,
        NOTE_ALREADY_SAVED_MESSAGE,
    ]


@pytest.mark.integration
async def test_repeated_voice_update_transcribes_once_and_creates_one_note(
    database_session: AsyncSession,
) -> None:
    message = make_message(
        300_005,
        voice=Voice(
            file_id="voice-file-id",
            file_unique_id="voice-file-unique-id",
            duration=2,
            mime_type="audio/ogg",
            file_size=5,
        ),
    )
    update = make_update(message, 430_001)
    service = MagicMock(spec=TranscriptionService)
    service.is_too_large.return_value = False
    service.transcribe = AsyncMock(return_value="Voice note content")
    bot = cast(Bot, MagicMock())

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await voice_message(
            message,
            bot,
            update,
            database_session,
            cast(TranscriptionService, service),
        )
        await voice_message(
            message,
            bot,
            update,
            database_session,
            cast(TranscriptionService, service),
        )

    notes = list(await database_session.scalars(select(Note)))
    assert len(notes) == 1
    assert notes[0].content == "Voice note content"
    assert notes[0].source == "voice"
    service.transcribe.assert_awaited_once()
    assert answer.await_args_list[-1].args[0] == NOTE_ALREADY_SAVED_MESSAGE
