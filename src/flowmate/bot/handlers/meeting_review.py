# ruff: noqa: RUF001
from pathlib import Path
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.errors import AIError
from flowmate.ai.provider import MeetingReviewProvider
from flowmate.ai.schemas import DraftSource
from flowmate.ai.service import DraftParsingService
from flowmate.db.models import MeetingReview, MeetingReviewItem
from flowmate.db.users import get_user_by_telegram_id
from flowmate.meetings.review import (
    MeetingReviewError,
    answer_review_item,
    confirm_review,
    generate_review,
    get_latest_review,
    get_review,
    review_revision,
    serialize_review,
)
from flowmate.speech.errors import AudioTooLargeError, SpeechError, SpeechTimeoutError
from flowmate.speech.service import TranscriptionService
from flowmate.task_engine.conversion import DraftConversionError, DraftConversionService


def review_keyboard(meeting_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Разобрать сейчас", callback_data=f"mr:clarify:{meeting_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Открыть итог", callback_data=f"mr:show:{meeting_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Подтвердить готовые", callback_data=f"mr:confirm:{meeting_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="Оставить в Inbox", callback_data=f"mr:inbox:{meeting_id}"
                )
            ],
        ]
    )


def format_review_summary(payload: dict[str, object]) -> str:
    counts = payload.get("counts", {})
    values = counts if isinstance(counts, dict) else {}
    labels = (
        ("task", "задачи"),
        ("follow_up", "follow-up"),
        ("waiting", "ожидания"),
        ("decision", "решения"),
        ("note", "заметки"),
    )
    lines = ["Встреча завершена.", "", "Записано:"]
    lines.extend(f"— {values.get(key, 0)} {label}" for key, label in labels)
    items = payload.get("items", [])
    unresolved = (
        sum(
            item.get("status") == "clarification_required"
            for item in items
            if isinstance(item, dict)
        )
        if isinstance(items, list)
        else 0
    )
    if unresolved:
        lines.extend(["", f"Требуют уточнения: {unresolved}."])
    return "\n".join(lines)


async def send_review_summary(
    message: Message, session: AsyncSession, review: MeetingReview
) -> None:
    payload = await serialize_review(session, review)
    await message.answer(
        format_review_summary(payload), reply_markup=review_keyboard(review.meeting_id)
    )


async def meeting_review_command(
    message: Message,
    db_session: AsyncSession,
    meeting_review_provider: MeetingReviewProvider | None,
    ai_high_confidence_threshold: float,
    ai_clarification_confidence_threshold: float,
) -> None:
    telegram_user = message.from_user
    user = (
        await get_user_by_telegram_id(db_session, telegram_user.id)
        if telegram_user is not None
        else None
    )
    review = await get_latest_review(db_session, user.id) if user is not None else None
    if review is None:
        await message.answer("Итога встречи пока нет.")
        return
    if user is None:
        return
    if review.status == "failed":
        meeting_id = review.meeting_id
        try:
            await generate_review(
                db_session,
                user.id,
                meeting_id,
                meeting_review_provider,
                high_threshold=ai_high_confidence_threshold,
                clarification_threshold=ai_clarification_confidence_threshold,
            )
            await db_session.commit()
            review = await get_review(db_session, user.id, meeting_id)
            if review is None:
                await message.answer("Итог встречи не найден.")
                return
        except MeetingReviewError:
            await db_session.commit()
            await message.answer("Итог пока недоступен. Попробуйте позже.")
            return
    await send_review_summary(message, db_session, review)


async def _next_question(
    message: Message, session: AsyncSession, review: MeetingReview
) -> bool:
    item = await session.scalar(
        select(MeetingReviewItem)
        .where(
            MeetingReviewItem.review_id == review.id,
            MeetingReviewItem.user_id == review.user_id,
            MeetingReviewItem.status == "clarification_required",
        )
        .order_by(MeetingReviewItem.position, MeetingReviewItem.id)
        .limit(1)
    )
    if item is None:
        review.current_item_id = None
        review.current_question = None
        review.current_question_message_id = None
        await session.flush()
        await message.answer("Критических уточнений больше нет.")
        return False
    question = item.clarification_question or f"Уточните пункт: {item.title}"
    prompt = await message.answer(question, reply_markup=ForceReply(selective=True))
    review.current_item_id = item.id
    review.current_question = question
    review.current_question_message_id = prompt.message_id
    await session.flush()
    return True


async def meeting_review_callback(
    callback: CallbackQuery,
    event_update: Update,
    db_session: AsyncSession,
    draft_conversion_service: DraftConversionService,
) -> None:
    message = callback.message
    if not isinstance(message, Message):
        await callback.answer()
        return
    try:
        _, action, raw_meeting_id = (callback.data or "").split(":", 2)
        meeting_id = UUID(raw_meeting_id)
        user = await get_user_by_telegram_id(db_session, callback.from_user.id)
        if user is None:
            raise MeetingReviewError("review not found")
        review = await get_review(db_session, user.id, meeting_id, for_update=True)
        if review is None:
            raise MeetingReviewError("review not found")
        if action == "show":
            await send_review_summary(message, db_session, review)
        elif action == "clarify":
            await _next_question(message, db_session, review)
        elif action in {"confirm", "inbox"}:
            await confirm_review(
                db_session,
                user.id,
                meeting_id,
                expected_revision=review_revision(review),
                telegram_update_id=event_update.update_id,
                move_incomplete_to_inbox=action == "inbox",
                conversion_service=draft_conversion_service,
            )
            await db_session.commit()
            refreshed = await get_review(db_session, user.id, meeting_id)
            if refreshed is not None:
                await send_review_summary(message, db_session, refreshed)
            await callback.answer()
            return
        else:
            raise MeetingReviewError("unsupported review action")
        await db_session.commit()
        await callback.answer()
    except (ValueError, MeetingReviewError, DraftConversionError, SQLAlchemyError):
        await db_session.rollback()
        await callback.answer("Не удалось обновить итог встречи.", show_alert=True)


async def meeting_review_reply(
    message: Message,
    bot: Bot,
    event_update: Update,
    db_session: AsyncSession,
    active_meeting_review: MeetingReview,
    transcription_service: TranscriptionService | None,
    draft_parsing_service: DraftParsingService | None,
) -> None:
    text = (message.text or "").strip()
    voice = message.voice
    if voice is not None:
        if transcription_service is None:
            await message.answer("Распознавание речи пока не настроено.")
            return
        if transcription_service.is_too_large(voice.file_size):
            await message.answer("Голосовое сообщение слишком большое.")
            return

        async def download_audio(destination: Path, timeout_seconds: int) -> None:
            await bot.download(voice, destination=destination, timeout=timeout_seconds)

        try:
            text = await transcription_service.transcribe(
                download_audio, reported_file_size=voice.file_size
            )
        except (
            AudioTooLargeError,
            SpeechTimeoutError,
            SpeechError,
            TelegramAPIError,
            OSError,
        ):
            await message.answer("Не удалось распознать ответ. Попробуйте ещё раз.")
            return
    if not text or active_meeting_review.current_item_id is None:
        await message.answer("Пришлите текстовый или голосовой ответ.")
        return
    if draft_parsing_service is None:
        await message.answer("AI-разбор уточнения пока не настроен.")
        return
    try:
        review = await answer_review_item(
            db_session,
            active_meeting_review.user_id,
            active_meeting_review.meeting_id,
            active_meeting_review.current_item_id,
            text,
            parsing_service=draft_parsing_service,
            answer_source=DraftSource.VOICE if voice is not None else DraftSource.TEXT,
            telegram_update_id=event_update.update_id,
        )
        await _next_question(message, db_session, review)
        await db_session.commit()
    except (AIError, MeetingReviewError, SQLAlchemyError):
        await db_session.rollback()
        await message.answer("Не удалось сохранить уточнение.")
