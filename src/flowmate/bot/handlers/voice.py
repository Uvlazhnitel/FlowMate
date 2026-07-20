# ruff: noqa: RUF001
import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, Update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.handlers.notes import (
    NOTE_ALREADY_SAVED_MESSAGE,
    NOTE_SAVE_FAILED_MESSAGE,
    NOTE_SAVED_MESSAGE,
    save_note_for_message,
)
from flowmate.db.notes import get_note_by_telegram_update_id
from flowmate.speech.errors import AudioTooLargeError, SpeechError, SpeechTimeoutError
from flowmate.speech.service import TranscriptionService

PROCESSING_MESSAGE = "Обрабатываю голосовое сообщение."
SPEECH_UNAVAILABLE_MESSAGE = "Распознавание речи пока не настроено."
OVERSIZED_MESSAGE = (
    "Голосовое сообщение слишком большое. Отправьте запись меньшего размера."
)
TRANSCRIPTION_FAILED_MESSAGE = (
    "Не удалось распознать голосовое сообщение. Попробуйте позже."
)
TELEGRAM_TEXT_LIMIT = 4000

logger = logging.getLogger(__name__)


def split_transcription(text: str, max_length: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_length:
        newline = remaining.rfind("\n", 0, max_length + 1)
        space = remaining.rfind(" ", 0, max_length + 1)
        boundary = max(newline, space)
        split_at = boundary + 1 if boundary >= max_length // 2 else max_length
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        chunks.append(remaining)
    return chunks


async def voice_message(
    message: Message,
    bot: Bot,
    event_update: Update,
    db_session: AsyncSession,
    transcription_service: TranscriptionService | None,
) -> None:
    voice = message.voice
    telegram_user = message.from_user
    if voice is None or telegram_user is None:
        return

    if transcription_service is None:
        await message.answer(SPEECH_UNAVAILABLE_MESSAGE)
        return
    if transcription_service.is_too_large(voice.file_size):
        await message.answer(OVERSIZED_MESSAGE)
        return

    try:
        existing_note = await get_note_by_telegram_update_id(
            db_session,
            event_update.update_id,
        )
        await db_session.rollback()
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_note_database_failed user_id=%s operation=duplicate_check",
            telegram_user.id,
        )
        await message.answer(NOTE_SAVE_FAILED_MESSAGE)
        return
    if existing_note is not None:
        await message.answer(NOTE_ALREADY_SAVED_MESSAGE)
        return

    await message.answer(PROCESSING_MESSAGE)

    async def download_audio(destination: Path, timeout_seconds: int) -> None:
        await bot.download(
            voice,
            destination=destination,
            timeout=timeout_seconds,
        )

    try:
        transcription = await transcription_service.transcribe(
            download_audio,
            reported_file_size=voice.file_size,
        )
    except AudioTooLargeError:
        logger.warning(
            "voice_transcription_rejected user_id=%s file_size=%s category=oversized",
            telegram_user.id,
            voice.file_size,
        )
        await message.answer(OVERSIZED_MESSAGE)
        return
    except SpeechTimeoutError:
        logger.warning(
            "voice_transcription_failed user_id=%s file_size=%s category=timeout",
            telegram_user.id,
            voice.file_size,
        )
        await message.answer(TRANSCRIPTION_FAILED_MESSAGE)
        return
    except (SpeechError, TelegramAPIError, OSError):
        logger.warning(
            "voice_transcription_failed user_id=%s file_size=%s category=provider",
            telegram_user.id,
            voice.file_size,
        )
        await message.answer(TRANSCRIPTION_FAILED_MESSAGE)
        return

    save_result = await save_note_for_message(
        message,
        event_update,
        db_session,
        content=transcription,
        source="voice",
    )
    if save_result == "failed":
        await message.answer(NOTE_SAVE_FAILED_MESSAGE)
        return
    if save_result == "duplicate":
        await message.answer(NOTE_ALREADY_SAVED_MESSAGE)
        return

    for chunk in split_transcription(transcription):
        await message.answer(chunk, parse_mode=None)
    await message.answer(NOTE_SAVED_MESSAGE)
