# ruff: noqa: RUF001
import logging
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.errors import AIError
from flowmate.ai.schemas import DraftSource, MeetingDraftContext
from flowmate.ai.service import DraftParsingService
from flowmate.bot.formatting import split_plain_text
from flowmate.bot.handlers.notes import NOTE_SAVE_FAILED_MESSAGE
from flowmate.bot.handlers.voice import (
    OVERSIZED_MESSAGE,
    PROCESSING_MESSAGE,
    SPEECH_UNAVAILABLE_MESSAGE,
    TRANSCRIPTION_FAILED_MESSAGE,
)
from flowmate.db.models import DraftSession, Meeting, Note
from flowmate.db.notes import create_note_idempotently, get_note_by_telegram_update_id
from flowmate.db.users import get_user_by_telegram_id
from flowmate.meetings.capture import (
    CaptureConflictError,
    create_capture,
    get_capture_by_note,
    get_owned_capture,
    list_captures,
    mark_capture_failed,
    remove_capture,
    save_capture_analysis,
)
from flowmate.meetings.service import get_recoverable_meeting, meeting_is_long_running
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.speech.errors import AudioTooLargeError, SpeechError, SpeechTimeoutError
from flowmate.speech.service import TranscriptionService

logger = logging.getLogger(__name__)


def capture_keyboard(capture_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Undo", callback_data=f"mc:undo:{capture_id}")]
        ]
    )


async def _acknowledge(message: Message, capture: DraftSession) -> None:
    await message.answer(
        f"✅ Записал. Пункт №{capture.capture_sequence}.",
        reply_markup=capture_keyboard(capture.id),
    )


def _meeting_ai_context(capture: DraftSession) -> MeetingDraftContext:
    context = capture.capture_context
    primary_id = context.get("primary_topic_id")
    topics = list(context.get("topics", []))
    primary = next(
        (value["name"] for value in topics if value.get("id") == primary_id), None
    )
    return MeetingDraftContext.model_validate(
        {
            "meeting_id": context["meeting_id"],
            "meeting_type": context["meeting_type"],
            "participants": [
                value["name"] for value in context.get("participants", [])
            ],
            "topics": [value["name"] for value in topics],
            "primary_topic": primary,
        }
    )


async def _analyze_capture(
    *,
    content: str,
    source: DraftSource,
    capture: DraftSession,
    service: DraftParsingService | None,
    db_session: AsyncSession,
    draft_ttl_hours: int,
) -> None:
    if service is None:
        await mark_capture_failed(db_session, capture)
        await db_session.commit()
        return
    try:
        timezone = ZoneInfo(str(capture.capture_context["timezone"]))
        captured_at = datetime.fromisoformat(
            str(capture.capture_context["captured_at"])
        )
        context = service.build_meeting_context(
            source=source,
            timezone=timezone,
            current_datetime=captured_at,
            meeting=_meeting_ai_context(capture),
        )
        analysis = await service.parse(content, source=source, context=context)
        await save_capture_analysis(
            db_session,
            capture,
            analysis,
            draft_ttl_hours=draft_ttl_hours,
        )
        await db_session.commit()
    except AIError as error:
        await db_session.rollback()
        logger.warning(
            "meeting_capture_analysis_failed category=%s", type(error).__name__
        )
        try:
            persisted = await get_capture_by_note(
                db_session, capture.user_id, capture.source_note_id
            )
            if persisted is not None:
                await mark_capture_failed(db_session, persisted)
                await db_session.commit()
        except SQLAlchemyError:
            await db_session.rollback()
    except (ValueError, SQLAlchemyError):
        await db_session.rollback()
        logger.error("meeting_capture_analysis_failed category=storage")


async def _save_capture(
    *,
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    active_meeting: Meeting,
    capture_user_id: UUID,
    content: str,
    source: DraftSource,
    notification_defaults: NotificationDefaults,
    draft_ttl_hours: int,
    captured_at: datetime,
) -> tuple[DraftSession | None, bool]:
    try:
        note, note_created = await create_note_idempotently(
            db_session,
            user_id=capture_user_id,
            content=content,
            source=source.value,
            telegram_update_id=event_update.update_id,
        )
        preferences = await get_effective_notification_preferences(
            db_session, capture_user_id, notification_defaults
        )
        capture, capture_created = await create_capture(
            db_session,
            user_id=capture_user_id,
            meeting_id=active_meeting.id,
            note=note,
            timezone=preferences.timezone,
            captured_at=captured_at,
            draft_ttl_hours=draft_ttl_hours,
        )
        await db_session.commit()
        return capture, note_created and capture_created
    except (ValueError, SQLAlchemyError):
        await db_session.rollback()
        await message.answer(NOTE_SAVE_FAILED_MESSAGE)
        return None, False


async def meeting_text_capture(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    active_meeting: Meeting,
    capture_user_id: UUID,
    notification_defaults: NotificationDefaults,
    draft_parsing_service: DraftParsingService | None = None,
    draft_ttl_hours: int = 24,
) -> None:
    content = (message.text or "").strip()
    if not content:
        await message.answer("Не удалось сохранить пустой пункт. Повторите сообщение.")
        return
    capture, created = await _save_capture(
        message=message,
        event_update=event_update,
        db_session=db_session,
        active_meeting=active_meeting,
        capture_user_id=capture_user_id,
        content=content,
        source=DraftSource.TEXT,
        notification_defaults=notification_defaults,
        draft_ttl_hours=draft_ttl_hours,
        captured_at=message.date,
    )
    if capture is None:
        return
    await _acknowledge(message, capture)
    if created:
        await _analyze_capture(
            content=content,
            source=DraftSource.TEXT,
            capture=capture,
            service=draft_parsing_service,
            db_session=db_session,
            draft_ttl_hours=draft_ttl_hours,
        )


async def meeting_voice_capture(
    message: Message,
    bot: Bot,
    event_update: Update,
    db_session: AsyncSession,
    active_meeting: Meeting,
    capture_user_id: UUID,
    notification_defaults: NotificationDefaults,
    transcription_service: TranscriptionService | None,
    draft_parsing_service: DraftParsingService | None = None,
    draft_ttl_hours: int = 24,
) -> None:
    voice = message.voice
    if voice is None:
        return
    try:
        existing_note = await get_note_by_telegram_update_id(
            db_session, event_update.update_id
        )
        existing = (
            await get_capture_by_note(db_session, capture_user_id, existing_note.id)
            if isinstance(existing_note, Note)
            else None
        )
        await db_session.rollback()
    except SQLAlchemyError:
        await db_session.rollback()
        await message.answer(NOTE_SAVE_FAILED_MESSAGE)
        return
    if existing is not None:
        await _acknowledge(message, existing)
        return
    if transcription_service is None:
        await message.answer(SPEECH_UNAVAILABLE_MESSAGE)
        return
    if transcription_service.is_too_large(voice.file_size):
        await message.answer(OVERSIZED_MESSAGE)
        return
    await message.answer(PROCESSING_MESSAGE)

    async def download_audio(destination: Path, timeout_seconds: int) -> None:
        await bot.download(voice, destination=destination, timeout=timeout_seconds)

    try:
        transcription = await transcription_service.transcribe(
            download_audio, reported_file_size=voice.file_size
        )
    except AudioTooLargeError:
        await message.answer(OVERSIZED_MESSAGE)
        return
    except (SpeechTimeoutError, SpeechError, TelegramAPIError, OSError):
        await message.answer(TRANSCRIPTION_FAILED_MESSAGE)
        return
    capture, created = await _save_capture(
        message=message,
        event_update=event_update,
        db_session=db_session,
        active_meeting=active_meeting,
        capture_user_id=capture_user_id,
        content=transcription,
        source=DraftSource.VOICE,
        notification_defaults=notification_defaults,
        draft_ttl_hours=draft_ttl_hours,
        captured_at=message.date,
    )
    if capture is None:
        return
    await _acknowledge(message, capture)
    if created:
        await _analyze_capture(
            content=transcription,
            source=DraftSource.VOICE,
            capture=capture,
            service=draft_parsing_service,
            db_session=db_session,
            draft_ttl_hours=draft_ttl_hours,
        )


async def meeting_capture_undo_callback(
    callback: CallbackQuery,
    db_session: AsyncSession,
) -> None:
    try:
        capture_id = UUID((callback.data or "").rsplit(":", 1)[1])
        user = await get_user_by_telegram_id(db_session, callback.from_user.id)
        if user is None:
            raise CaptureConflictError("user not found")
        capture = await get_capture_by_note_for_id(db_session, user.id, capture_id)
        if capture is None or capture.meeting_id is None:
            raise CaptureConflictError("capture not found")
        await remove_capture(
            db_session,
            user.id,
            capture.meeting_id,
            capture.id,
            latest_only=True,
        )
        await db_session.commit()
        await callback.answer("Последний пункт отменён.")
    except (ValueError, SQLAlchemyError):
        await db_session.rollback()
        await callback.answer("Можно отменить только последний пункт.", show_alert=True)


async def get_capture_by_note_for_id(
    session: AsyncSession, user_id: UUID, capture_id: UUID
) -> DraftSession | None:
    meeting_id = await session.scalar(
        select(DraftSession.meeting_id).where(
            DraftSession.id == capture_id, DraftSession.user_id == user_id
        )
    )
    if meeting_id is None:
        return None
    return await get_owned_capture(session, user_id, meeting_id, capture_id)


async def meeting_notes_command(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await message.answer("Нет встречи с сохранёнными пунктами.")
        return
    meeting = await get_recoverable_meeting(db_session, user.id)
    if meeting is None:
        await message.answer("Нет встречи с сохранёнными пунктами.")
        return
    page = await list_captures(db_session, user.id, meeting.id, limit=50, offset=0)
    warning = (
        "\n⚠️ Встреча активна больше 12 часов."
        if meeting_is_long_running(meeting)
        else ""
    )
    lines = [f"{meeting.title}{warning}"]
    for capture in page.items:
        items = cast(list[dict[str, object]], capture.get("items", []))
        titles = "; ".join(str(item["title"]) for item in items) or "обрабатывается"
        lines.append(f"№{capture['sequence']}: {titles}")
    if len(lines) == 1:
        lines.append("Пунктов пока нет.")
    for chunk in split_plain_text("\n".join(lines)):
        await message.answer(chunk, parse_mode=None)
