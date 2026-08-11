# ruff: noqa: RUF001
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, Update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.errors import AIError
from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftSource,
    ManagementIntent,
    SearchIntent,
)
from flowmate.ai.service import DraftParsingService
from flowmate.bot.formatting import TELEGRAM_TEXT_LIMIT, split_plain_text
from flowmate.bot.handlers.drafts import (
    DRAFT_ANALYZING_MESSAGE,
    DRAFT_RETRY_MESSAGE,
    analyze_note_content,
)
from flowmate.bot.handlers.navigation.search import execute_search_intent
from flowmate.bot.handlers.notes import (
    NOTE_ALREADY_SAVED_MESSAGE,
    NOTE_SAVE_FAILED_MESSAGE,
    NOTE_SAVED_MESSAGE,
    capture_workspace_override,
    explicitly_manages_existing_item,
    save_note_for_message,
    selected_capture_workspace,
)
from flowmate.bot.handlers.work_items.management import (
    ManagementIntentOutcome,
    execute_management_intent,
)
from flowmate.db.models import WorkItemActionSession
from flowmate.db.notes import get_note_by_telegram_update_id
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.reminders.sync import ReminderPolicy
from flowmate.speech.errors import AudioTooLargeError, SpeechError, SpeechTimeoutError
from flowmate.speech.service import TranscriptionService
from flowmate.task_engine.conversion import DraftConversionService
from flowmate.task_engine.rescheduling import ReschedulingService
from flowmate.workspaces import active_workspace

PROCESSING_MESSAGE = "Обрабатываю голосовое сообщение."
SPEECH_UNAVAILABLE_MESSAGE = "Распознавание речи пока не настроено."
OVERSIZED_MESSAGE = (
    "Голосовое сообщение слишком большое. Отправьте запись меньшего размера."
)
TRANSCRIPTION_FAILED_MESSAGE = (
    "Не удалось распознать голосовое сообщение. Попробуйте позже."
)
logger = logging.getLogger(__name__)


def split_transcription(text: str, max_length: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    return split_plain_text(text, max_length)


async def voice_message(
    message: Message,
    bot: Bot,
    event_update: Update,
    db_session: AsyncSession,
    transcription_service: TranscriptionService | None,
    draft_parsing_service: DraftParsingService | None = None,
    draft_ttl_hours: int = 24,
    default_workspace: str = "personal",
    active_capture: WorkItemActionSession | None = None,
    ai_high_confidence_threshold: float = 0.8,
    draft_conversion_service: DraftConversionService | None = None,
    notification_defaults: NotificationDefaults | None = None,
    work_item_action_ttl_minutes: int = 30,
    app_timezone: ZoneInfo | None = None,
    reminder_policy: ReminderPolicy | None = None,
    rescheduling_service: ReschedulingService | None = None,
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

    parse_content, explicit_workspace = capture_workspace_override(transcription)
    routed: DraftAnalysisResult | ManagementIntent | SearchIntent | None = None
    transcription_shown = False
    if draft_parsing_service is not None:
        try:
            workspace = active_workspace(db_session)
            routed = (
                await draft_parsing_service.parse(
                    parse_content,
                    source=DraftSource.VOICE,
                    active_workspace=workspace,
                )
                if active_capture is not None
                else await draft_parsing_service.parse_text(
                    parse_content,
                    active_workspace=workspace,
                )
                if workspace is not None
                else await draft_parsing_service.parse_text(parse_content)
            )
        except AIError as error:
            logger.warning(
                "telegram_voice_routing_failed user_id=%s category=%s",
                telegram_user.id,
                type(error).__name__,
            )
    if isinstance(routed, ManagementIntent):
        for chunk in split_transcription(transcription):
            await message.answer(chunk, parse_mode=None)
        transcription_shown = True
        try:
            outcome = await execute_management_intent(
                message,
                event_update,
                db_session,
                routed,
                high_confidence_threshold=ai_high_confidence_threshold,
                action_ttl_minutes=work_item_action_ttl_minutes,
                app_timezone=app_timezone or ZoneInfo("UTC"),
                reminder_policy=reminder_policy,
                notification_defaults=notification_defaults,
                rescheduling_service=rescheduling_service,
            )
        except SQLAlchemyError:
            await db_session.rollback()
            logger.error(
                "telegram_voice_management_database_failed user_id=%s",
                telegram_user.id,
            )
            await message.answer("Не удалось изменить запись. Попробуйте позже.")
            return
        if outcome is not ManagementIntentOutcome.NOT_FOUND:
            return
        if explicitly_manages_existing_item(message, transcription):
            await message.answer("Подходящая запись не найдена.")
            return
        if draft_parsing_service is None:
            return
        try:
            routed = await draft_parsing_service.parse(
                parse_content,
                source=DraftSource.VOICE,
                active_workspace=active_workspace(db_session),
            )
        except AIError:
            routed = None
    if isinstance(routed, SearchIntent):
        for chunk in split_transcription(transcription):
            await message.answer(chunk, parse_mode=None)
        transcription_shown = True
        try:
            await execute_search_intent(
                message,
                event_update,
                db_session,
                routed,
                high_confidence_threshold=ai_high_confidence_threshold,
                action_ttl_minutes=work_item_action_ttl_minutes,
                timezone=app_timezone or ZoneInfo("UTC"),
            )
        except SQLAlchemyError:
            await db_session.rollback()
            logger.error(
                "telegram_voice_search_database_failed user_id=%s",
                telegram_user.id,
            )
            await message.answer("Не удалось выполнить поиск. Попробуйте позже.")
        return

    analysis = routed if isinstance(routed, DraftAnalysisResult) else None
    current_workspace = active_workspace(db_session) or default_workspace
    selected_workspace, update_workspace = selected_capture_workspace(
        analysis,
        current=current_workspace,
        explicit=explicit_workspace,
        high_confidence_threshold=ai_high_confidence_threshold,
    )
    save_result = await save_note_for_message(
        message,
        event_update,
        db_session,
        content=transcription,
        source="voice",
        create_draft=draft_parsing_service is not None,
        draft_ttl_hours=draft_ttl_hours,
        default_workspace=default_workspace,
        capture_session=active_capture,
        workspace_override=selected_workspace,
        update_active_workspace=update_workspace,
    )
    if save_result.status == "failed":
        await message.answer(NOTE_SAVE_FAILED_MESSAGE)
        return
    if save_result.status == "duplicate":
        await message.answer(NOTE_ALREADY_SAVED_MESSAGE)
        return

    if not transcription_shown:
        for chunk in split_transcription(transcription):
            await message.answer(chunk, parse_mode=None)
    if draft_parsing_service is None or save_result.draft is None:
        await message.answer(NOTE_SAVED_MESSAGE)
        return

    await message.answer(DRAFT_ANALYZING_MESSAGE)
    await analyze_note_content(
        message,
        content=parse_content,
        telegram_user_id=telegram_user.id,
        source=DraftSource.VOICE,
        service=draft_parsing_service,
        db_session=db_session,
        draft=save_result.draft,
        draft_ttl_hours=draft_ttl_hours,
        precomputed_result=analysis,
        active_workspace=save_result.draft.workspace,
        high_confidence_threshold=ai_high_confidence_threshold,
        draft_conversion_service=draft_conversion_service,
        notification_defaults=notification_defaults,
        failure_message=DRAFT_RETRY_MESSAGE,
    )
