# ruff: noqa: RUF001
import logging
from datetime import datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    Message,
    Update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.errors import AIError
from flowmate.bot.callback_data import (
    decode_revision as decode_revision,
)
from flowmate.bot.callback_data import (
    encode_revision as encode_revision,
)
from flowmate.bot.handlers.navigation.search import complete_search_action
from flowmate.bot.menu import answer_with_main_menu
from flowmate.db.models import WorkItemActionSession
from flowmate.reminders.actions import (
    snooze_work_item_reminder,
)
from flowmate.reminders.parsing import SnoozeParsingError, SnoozeParsingService
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.sync import ReminderPolicy
from flowmate.speech.errors import AudioTooLargeError, SpeechError, SpeechTimeoutError
from flowmate.speech.service import TranscriptionService
from flowmate.task_engine.action_sessions import (
    finish_action_session,
)
from flowmate.task_engine.details import get_work_item_details
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.intents import (
    AmbiguousManagementCandidateError,
    resolve_person_candidate,
    resolve_topic_candidate,
)
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    add_work_item_note,
    change_work_item_person,
    change_work_item_topic,
    update_work_item_content,
)
from flowmate.task_engine.rescheduling import (
    ReschedulingService,
    effective_schedule,
)

from .cards import details_keyboard, format_datetime, format_work_item_details

logger = logging.getLogger(__name__)


LIST_FAILED_MESSAGE = "Не удалось загрузить записи. Попробуйте позже."


async def action_session_message(
    message: Message,
    bot: Bot,
    event_update: Update,
    db_session: AsyncSession,
    active_work_item_action: WorkItemActionSession,
    action_user_id: UUID,
    app_timezone: ZoneInfo,
    notification_defaults: NotificationDefaults,
    snooze_parsing_service: SnoozeParsingService,
    transcription_service: TranscriptionService | None,
    reminder_policy: ReminderPolicy | None = None,
    rescheduling_service: ReschedulingService | None = None,
) -> None:
    action = WorkItemAction(active_work_item_action.action)
    text = message.text.strip() if message.text else ""
    voice = message.voice
    if action is WorkItemAction.SEARCH:
        if voice is not None:
            await message.answer("Введите поисковый запрос текстом.")
            return
        if not text:
            await message.answer("Нужен текстовый поисковый запрос.")
            return
        try:
            await complete_search_action(
                message,
                db_session,
                active_work_item_action,
                action_user_id,
                query=text,
                timezone=app_timezone,
                telegram_update_id=event_update.update_id,
            )
        except ValueError:
            await db_session.rollback()
            await message.answer("Введите поисковый запрос длиной до 200 символов.")
        except SQLAlchemyError:
            await db_session.rollback()
            await message.answer(LIST_FAILED_MESSAGE)
        return
    if voice is not None and action in {
        WorkItemAction.REMINDER_SNOOZE,
        WorkItemAction.RESCHEDULE,
        WorkItemAction.EDIT_FIELD,
    }:
        if transcription_service is None:
            await message.answer("Распознавание речи пока не настроено.")
            return
        if transcription_service.is_too_large(voice.file_size):
            await message.answer("Голосовое сообщение слишком большое.")
            return

        async def download_audio(destination: Path, timeout_seconds: int) -> None:
            await bot.download(
                voice,
                destination=destination,
                timeout=timeout_seconds,
            )

        try:
            text = await transcription_service.transcribe(
                download_audio,
                reported_file_size=voice.file_size,
            )
        except (
            AudioTooLargeError,
            SpeechTimeoutError,
            SpeechError,
            TelegramAPIError,
            OSError,
        ):
            logger.warning(
                "work_item_action_voice_failed user_id=%s category=transcription",
                message.from_user.id if message.from_user else 0,
            )
            await message.answer("Не удалось распознать ответ. Попробуйте текстом.")
            return
    if not text or active_work_item_action.work_item_id is None:
        await message.answer("Нужен текстовый ответ.")
        return
    item_id = active_work_item_action.work_item_id
    try:
        if action is WorkItemAction.REMINDER_SNOOZE:
            reminder_id_value = active_work_item_action.context.get("reminder_id")
            if reminder_id_value is None:
                raise ValueError("reminder context is missing")
            preferences = await get_effective_notification_preferences(
                db_session, action_user_id, notification_defaults
            )
            new_date = await snooze_parsing_service.parse(
                text,
                timezone=preferences.zoneinfo,
                now=datetime.now(preferences.zoneinfo),
                default_time=preferences.default_reminder_time,
            )
            await snooze_work_item_reminder(
                db_session,
                action_user_id,
                UUID(str(reminder_id_value)),
                event_update.update_id,
                until=new_date,
                expected_revision=(
                    int(active_work_item_action.context["reminder_revision"])
                    if "reminder_revision" in active_work_item_action.context
                    else None
                ),
            )
            response = (
                "Напоминание отложено до "
                f"{format_datetime(new_date, preferences.zoneinfo)}."
            )
        elif action is WorkItemAction.RESCHEDULE:
            preferences = await get_effective_notification_preferences(
                db_session, action_user_id, notification_defaults
            )
            service = rescheduling_service or ReschedulingService(
                snooze_parsing_service
            )
            result = await service.reschedule_text(
                db_session,
                action_user_id,
                item_id,
                event_update.update_id,
                text,
                preferences=preferences,
                reminder_policy=reminder_policy,
                expected_revision=(
                    int(active_work_item_action.context["work_item_revision"])
                    if "work_item_revision" in active_work_item_action.context
                    else None
                ),
            )
            rescheduled_at = effective_schedule(result.work_item)
            if rescheduled_at is None:
                raise RuntimeError("rescheduled work item has no effective date")
            response = (
                f"Запись перенесена на "
                f"{format_datetime(rescheduled_at, preferences.zoneinfo)}."
            )
        elif action is WorkItemAction.ADD_NOTE:
            await add_work_item_note(
                db_session,
                action_user_id,
                item_id,
                event_update.update_id,
                text,
                expected_revision=(
                    int(active_work_item_action.context["work_item_revision"])
                    if "work_item_revision" in active_work_item_action.context
                    else None
                ),
            )
            response = "Заметка добавлена."
        elif action is WorkItemAction.EDIT_FIELD:
            field = active_work_item_action.context.get("edit_field")
            if field not in {"title", "description"}:
                raise ValueError("edit field is invalid")
            await update_work_item_content(
                db_session,
                action_user_id,
                item_id,
                event_update.update_id,
                title=text if field == "title" else None,
                description=text if field == "description" else None,
                update_title=field == "title",
                update_description=field == "description",
                expected_revision=(
                    int(active_work_item_action.context["work_item_revision"])
                    if "work_item_revision" in active_work_item_action.context
                    else None
                ),
            )
            response = (
                "Название изменено." if field == "title" else "Описание изменено."
            )
        elif action is WorkItemAction.CHANGE_TOPIC:
            topic_id: UUID | None = None
            if text.casefold() not in {"без темы", "none", "нет"}:
                topic = await resolve_topic_candidate(
                    db_session,
                    action_user_id,
                    text,
                )
                topic_id = topic.id
            await change_work_item_topic(
                db_session,
                action_user_id,
                item_id,
                event_update.update_id,
                topic_id,
            )
            response = "Тема изменена."
        elif action in {WorkItemAction.ADD_PERSON, WorkItemAction.REPLACE_PERSON}:
            person = await resolve_person_candidate(
                db_session,
                action_user_id,
                text,
            )
            await change_work_item_person(
                db_session,
                action_user_id,
                item_id,
                event_update.update_id,
                person.id,
                replace_person_id=None,
            )
            response = "Человек добавлен."
        else:
            await message.answer("Действие больше недоступно.")
            return
        await finish_action_session(db_session, active_work_item_action)
        await db_session.commit()
        origin_chat_id = active_work_item_action.context.get("origin_chat_id")
        origin_message_id = active_work_item_action.context.get("origin_message_id")
        if isinstance(origin_chat_id, int) and isinstance(origin_message_id, int):
            details = await get_work_item_details(db_session, action_user_id, item_id)
            if details is not None:
                try:
                    await bot.edit_message_text(
                        format_work_item_details(details, app_timezone),
                        chat_id=origin_chat_id,
                        message_id=origin_message_id,
                        parse_mode="HTML",
                        reply_markup=details_keyboard(details),
                    )
                except TelegramAPIError:
                    logger.warning(
                        "telegram_work_item_card_refresh_failed user_id=%s",
                        message.from_user.id if message.from_user else 0,
                    )
        await answer_with_main_menu(message, response)
    except AmbiguousManagementCandidateError as error:
        await db_session.rollback()
        message_text = (
            "Найдено несколько тем. Уточните название."
            if "topic" in str(error)
            else "Найдено несколько людей. Уточните имя."
        )
        await message.answer(message_text)
    except (AIError, InvalidWorkItemTransitionError, SnoozeParsingError, ValueError):
        await db_session.rollback()
        if action in {WorkItemAction.RESCHEDULE, WorkItemAction.REMINDER_SNOOZE}:
            await message.answer(
                "Не удалось понять дату. Можно написать: завтра утром, через час "
                "или 15 августа в 14:00. Для отмены нажмите ❌ Отмена."
            )
        else:
            await message.answer(
                "Не удалось сохранить изменение. Проверьте ответ и попробуйте снова."
            )
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_work_item_input_failed user_id=%s action=%s",
            message.from_user.id if message.from_user else 0,
            action.value,
        )
        await message.answer("Не удалось сохранить изменение. Попробуйте позже.")
