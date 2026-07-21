import logging
from pathlib import Path
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, Update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import DraftSource
from flowmate.ai.service import DraftParsingService
from flowmate.bot.handlers.drafts import (
    DRAFT_BUSY_MESSAGE,
    DRAFT_CONVERSION_FAILED_MESSAGE,
    DRAFT_EXPIRED_MESSAGE,
    DRAFT_FAILED_MESSAGE,
    DRAFT_REPLY_REQUIRED_MESSAGE,
    refine_draft,
)
from flowmate.bot.handlers.voice import (
    OVERSIZED_MESSAGE,
    SPEECH_UNAVAILABLE_MESSAGE,
    TRANSCRIPTION_FAILED_MESSAGE,
)
from flowmate.db.drafts import (
    DraftStatus,
    claim_update,
    clear_processing_update,
    transition_draft,
)
from flowmate.db.models import DraftSession
from flowmate.speech.errors import AudioTooLargeError, SpeechError, SpeechTimeoutError
from flowmate.speech.service import TranscriptionService
from flowmate.task_engine.conversion import (
    DraftConversionError,
    DraftConversionService,
    conversion_summary,
)

logger = logging.getLogger(__name__)


def is_reply_to_current_question(message: Message, draft: DraftSession) -> bool:
    replied = message.reply_to_message
    return (
        replied is not None
        and draft.current_question_message_id is not None
        and replied.message_id == draft.current_question_message_id
    )


async def handle_control_phrase(
    message: Message,
    draft: DraftSession,
    db_session: AsyncSession,
    *,
    update_id: int,
    user_id: UUID,
    draft_ttl_hours: int,
    draft_conversion_service: DraftConversionService,
) -> bool:
    normalized = " ".join((message.text or "").split()).casefold()
    if normalized not in {"отмена", "сохрани как есть"}:
        return False
    claim, claimed = await claim_update(
        db_session,
        draft_id=draft.id,
        user_id=user_id,
        update_id=update_id,
        ttl_hours=draft_ttl_hours,
    )
    if claim == "duplicate":
        await db_session.rollback()
        return True
    if claim == "busy":
        await db_session.rollback()
        await message.answer(DRAFT_BUSY_MESSAGE)
        return True
    if claim != "claimed" or claimed is None:
        await db_session.rollback()
        await message.answer(DRAFT_EXPIRED_MESSAGE)
        return True
    if normalized == "отмена":
        status: DraftStatus = "cancelled"
        await transition_draft(db_session, claimed, status)
        await db_session.commit()
        response = "Черновик отменён. Исходная заметка сохранена."
    else:
        try:
            result = await draft_conversion_service.convert(
                db_session,
                draft_id=claimed.id,
                user_id=user_id,
                allow_incomplete=True,
            )
            await db_session.commit()
            response = conversion_summary(result)
        except (DraftConversionError, SQLAlchemyError) as error:
            await db_session.rollback()
            logger.error(
                "telegram_draft_conversion_failed user_id=%s draft_id=%s category=%s",
                message.from_user.id if message.from_user is not None else "unknown",
                claimed.id,
                type(error).__name__,
            )
            response = DRAFT_CONVERSION_FAILED_MESSAGE
    await message.answer(response)
    return True


async def transcribe_clarification(
    message: Message,
    bot: Bot,
    service: TranscriptionService | None,
) -> str | None:
    voice = message.voice
    telegram_user = message.from_user
    if voice is None or telegram_user is None:
        return None
    if service is None:
        await message.answer(SPEECH_UNAVAILABLE_MESSAGE)
        return None
    if service.is_too_large(voice.file_size):
        await message.answer(OVERSIZED_MESSAGE)
        return None

    async def download_audio(destination: Path, timeout_seconds: int) -> None:
        await bot.download(voice, destination=destination, timeout=timeout_seconds)

    try:
        return await service.transcribe(
            download_audio,
            reported_file_size=voice.file_size,
        )
    except AudioTooLargeError:
        await message.answer(OVERSIZED_MESSAGE)
    except (SpeechTimeoutError, SpeechError, TelegramAPIError, OSError) as error:
        logger.warning(
            "draft_voice_answer_failed user_id=%s category=%s",
            telegram_user.id,
            type(error).__name__,
        )
        await message.answer(TRANSCRIPTION_FAILED_MESSAGE)
    return None


async def active_draft_message(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    bot: Bot,
    draft_user_id: UUID | None = None,
    active_draft: DraftSession | None = None,
    expired_draft: DraftSession | None = None,
    transcription_service: TranscriptionService | None = None,
    draft_parsing_service: DraftParsingService | None = None,
    draft_conversion_service: DraftConversionService | None = None,
    draft_ttl_hours: int = 24,
    draft_database_failed: bool = False,
    processed_draft_update: bool = False,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    if processed_draft_update:
        return
    if draft_database_failed:
        logger.error(
            "telegram_draft_database_failed user_id=%s operation=lookup",
            telegram_user.id,
        )
        await message.answer(DRAFT_FAILED_MESSAGE)
        return
    if expired_draft is not None or active_draft is None or draft_user_id is None:
        await message.answer(DRAFT_EXPIRED_MESSAGE)
        return
    if active_draft.status == "parsing":
        await message.answer("Черновик ещё анализируется.")
        return
    if await handle_control_phrase(
        message,
        active_draft,
        db_session,
        update_id=event_update.update_id,
        user_id=draft_user_id,
        draft_ttl_hours=draft_ttl_hours,
        draft_conversion_service=(draft_conversion_service or DraftConversionService()),
    ):
        return
    if active_draft.status == "ready":
        await message.answer("Сначала подтвердите, измените или отмените /draft.")
        return
    if not is_reply_to_current_question(message, active_draft):
        await message.answer(DRAFT_REPLY_REQUIRED_MESSAGE)
        return
    if draft_parsing_service is None:
        await message.answer(DRAFT_FAILED_MESSAGE)
        return

    answer_source = DraftSource.TEXT
    answer = (message.text or "").strip()
    if message.voice is not None:
        claim, claimed = await claim_update(
            db_session,
            draft_id=active_draft.id,
            user_id=draft_user_id,
            update_id=event_update.update_id,
            ttl_hours=draft_ttl_hours,
        )
        if claim == "duplicate":
            await db_session.rollback()
            return
        if claim == "busy":
            await db_session.rollback()
            await message.answer(DRAFT_BUSY_MESSAGE)
            return
        if claim != "claimed" or claimed is None:
            await db_session.rollback()
            await message.answer(DRAFT_EXPIRED_MESSAGE)
            return
        await db_session.commit()
        answer = (
            await transcribe_clarification(
                message,
                bot,
                transcription_service,
            )
            or ""
        )
        if not answer:
            await clear_processing_update(db_session, claimed)
            await db_session.commit()
            return
        answer_source = DraftSource.VOICE
        await refine_draft(
            message,
            draft=claimed,
            answer=answer,
            answer_source=answer_source,
            update_id=event_update.update_id,
            user_id=draft_user_id,
            telegram_user_id=telegram_user.id,
            service=draft_parsing_service,
            db_session=db_session,
            draft_ttl_hours=draft_ttl_hours,
            already_claimed=True,
        )
        return

    if not answer:
        await message.answer(DRAFT_REPLY_REQUIRED_MESSAGE)
        return
    try:
        await refine_draft(
            message,
            draft=active_draft,
            answer=answer,
            answer_source=answer_source,
            update_id=event_update.update_id,
            user_id=draft_user_id,
            telegram_user_id=telegram_user.id,
            service=draft_parsing_service,
            db_session=db_session,
            draft_ttl_hours=draft_ttl_hours,
        )
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_draft_database_failed user_id=%s operation=answer",
            telegram_user.id,
        )
        await message.answer(DRAFT_FAILED_MESSAGE)
