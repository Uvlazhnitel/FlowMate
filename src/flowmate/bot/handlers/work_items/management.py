# ruff: noqa: RUF001
import logging
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import ManagementAction, ManagementIntent, TemporalStatus
from flowmate.bot.callback_data import (
    decode_revision as decode_revision,
)
from flowmate.bot.callback_data import (
    encode_revision as encode_revision,
)
from flowmate.db.drafts import get_active_draft_for_user
from flowmate.db.models import WorkItem
from flowmate.db.users import get_user_by_telegram_id
from flowmate.reminders.parsing import SnoozeParsingService
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.sync import ReminderPolicy
from flowmate.task_engine.action_sessions import (
    create_action_session,
)
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.intents import (
    AmbiguousManagementCandidateError,
    find_intent_targets,
    resolve_person_candidate,
    resolve_replaced_person_id,
    resolve_topic_candidate,
)
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    add_work_item_note,
    cancel_work_item,
    change_work_item_person,
    change_work_item_topic,
    complete_work_item,
    mark_waiting_received,
    reopen_work_item,
    reschedule_work_item,
    update_work_item_content,
)
from flowmate.task_engine.queries import enrich_work_item_list
from flowmate.task_engine.rescheduling import (
    ReschedulingService,
)

from .cards import (
    item_action_data,
    item_keyboard,
    parse_work_item_callback,
    send_details,
)
from .editing import start_input_session
from .selection_presentation import format_selection_entry, selection_keyboard

logger = logging.getLogger(__name__)


class ManagementIntentOutcome(StrEnum):
    HANDLED = "handled"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


def replied_work_item_id(message: Message) -> UUID | None:
    replied = message.reply_to_message
    if replied is None or replied.reply_markup is None:
        return None
    for row in replied.reply_markup.inline_keyboard:
        for button in row:
            parsed = parse_work_item_callback(button.callback_data)
            if parsed is not None and not parsed[0].startswith("z"):
                return parsed[1]
    return None


async def apply_management_intent(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    *,
    user_id: UUID,
    telegram_user_id: int,
    item: WorkItem,
    intent: ManagementIntent,
    action_ttl_minutes: int,
    app_timezone: ZoneInfo,
    reminder_policy: ReminderPolicy | None = None,
    notification_defaults: NotificationDefaults | None = None,
    rescheduling_service: ReschedulingService | None = None,
) -> None:
    update_id = event_update.update_id
    try:
        if intent.action is ManagementAction.COMPLETE:
            await complete_work_item(
                db_session, user_id, item.id, telegram_update_id=update_id
            )
            response = f"✅ Выполнено: {item.title}"
        elif intent.action is ManagementAction.CANCEL:
            await cancel_work_item(
                db_session, user_id, item.id, telegram_update_id=update_id
            )
            response = "Запись отменена."
        elif intent.action is ManagementAction.REOPEN:
            await reopen_work_item(
                db_session, user_id, item.id, telegram_update_id=update_id
            )
            response = "Запись возвращена во входящие."
        elif intent.action is ManagementAction.WAITING_RECEIVED:
            await mark_waiting_received(
                db_session, user_id, item.id, telegram_update_id=update_id
            )
            response = "Ожидание завершено: результат получен."
        elif intent.action is ManagementAction.RESCHEDULE:
            temporal = intent.temporal_candidate
            if (
                temporal is None
                or temporal.status is not TemporalStatus.RESOLVED
                or temporal.normalized_value is None
            ):
                await start_input_session(
                    message,
                    db_session,
                    user_id=user_id,
                    item_id=item.id,
                    action=WorkItemAction.RESCHEDULE,
                    prompt=(
                        "Когда перенести? Можно написать: завтра утром, через час "
                        "или 15 августа в 14:00. Для отмены нажмите ❌ Отмена."
                    ),
                    ttl_minutes=action_ttl_minutes,
                    telegram_update_id=update_id,
                )
                return
            if notification_defaults is not None:
                preferences = await get_effective_notification_preferences(
                    db_session,
                    user_id,
                    notification_defaults,
                )
                service = rescheduling_service or ReschedulingService(
                    SnoozeParsingService(None, timeout_seconds=1)
                )
                await service.reschedule_text(
                    db_session,
                    user_id,
                    item.id,
                    update_id,
                    temporal.original_phrase,
                    preferences=preferences,
                    reminder_policy=reminder_policy,
                )
            else:
                await reschedule_work_item(
                    db_session,
                    user_id,
                    item.id,
                    update_id,
                    temporal.normalized_value,
                    reminder_policy=reminder_policy,
                )
            response = "Дата изменена."
        elif intent.action is ManagementAction.ADD_NOTE:
            if intent.note_text is None:
                await start_input_session(
                    message,
                    db_session,
                    user_id=user_id,
                    item_id=item.id,
                    action=WorkItemAction.ADD_NOTE,
                    prompt="Какую заметку добавить?",
                    ttl_minutes=action_ttl_minutes,
                    telegram_update_id=update_id,
                )
                return
            await add_work_item_note(
                db_session,
                user_id,
                item.id,
                update_id,
                intent.note_text,
            )
            response = "Заметка добавлена."
        elif intent.action is ManagementAction.CHANGE_TOPIC and intent.topic_candidate:
            topic = await resolve_topic_candidate(
                db_session,
                user_id,
                intent.topic_candidate,
            )
            await change_work_item_topic(
                db_session, user_id, item.id, update_id, topic.id
            )
            response = "Тема изменена."
        elif (
            intent.action
            in {ManagementAction.ADD_PERSON, ManagementAction.REPLACE_PERSON}
            and intent.person_candidate
        ):
            person = await resolve_person_candidate(
                db_session,
                user_id,
                intent.person_candidate,
            )
            replace_person_id: UUID | None = None
            if intent.action is ManagementAction.REPLACE_PERSON:
                replace_person_id = await resolve_replaced_person_id(
                    db_session,
                    user_id,
                    item.id,
                )
            await change_work_item_person(
                db_session,
                user_id,
                item.id,
                update_id,
                person.id,
                replace_person_id=replace_person_id,
            )
            response = "Человек добавлен."
        elif intent.action in {
            ManagementAction.CHANGE_TITLE,
            ManagementAction.CHANGE_DESCRIPTION,
        }:
            field = (
                "title"
                if intent.action is ManagementAction.CHANGE_TITLE
                else "description"
            )
            if intent.replacement_text is None and not (
                field == "description" and intent.clear_description
            ):
                await start_input_session(
                    message,
                    db_session,
                    user_id=user_id,
                    item_id=item.id,
                    action=WorkItemAction.EDIT_FIELD,
                    prompt=(
                        "Отправьте новое название текстом или голосом."
                        if field == "title"
                        else "Отправьте новое описание текстом или голосом."
                    ),
                    ttl_minutes=action_ttl_minutes,
                    telegram_update_id=update_id,
                    context={"edit_field": field},
                )
                return
            await update_work_item_content(
                db_session,
                user_id,
                item.id,
                update_id,
                title=(intent.replacement_text if field == "title" else None),
                description=(
                    intent.replacement_text
                    if field == "description" and not intent.clear_description
                    else None
                ),
                update_title=field == "title",
                update_description=field == "description",
            )
            response = (
                "Название изменено." if field == "title" else "Описание изменено."
            )
        elif intent.action is ManagementAction.SHOW_DETAILS:
            await send_details(message, db_session, user_id, item, app_timezone)
            return
        else:
            await message.answer(
                "Нужно уточнить данные. Откройте запись и выберите действие.",
                reply_markup=item_keyboard(item),
            )
            return
        await db_session.commit()
        await message.answer(response)
        if intent.action is ManagementAction.WAITING_RECEIVED:
            await db_session.refresh(item, attribute_names=["updated_at"])
            await message.answer(
                "Создать follow-up по результату?",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Создать follow-up",
                                callback_data=item_action_data("f", item),
                            )
                        ]
                    ]
                ),
            )
    except AmbiguousManagementCandidateError as error:
        await db_session.rollback()
        if "replacement" in str(error):
            response = "Для замены у записи должен быть ровно один человек."
        elif "topic" in str(error):
            response = "Найдено несколько тем. Уточните название."
        else:
            response = "Найдено несколько людей. Уточните имя."
        await message.answer(response)
    except (InvalidWorkItemTransitionError, ValueError):
        await db_session.rollback()
        await message.answer("Это изменение сейчас недоступно.")
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_management_failed user_id=%s action=%s",
            telegram_user_id,
            intent.action.value,
        )
        await message.answer("Не удалось изменить запись. Попробуйте позже.")


async def execute_management_intent(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    intent: ManagementIntent,
    *,
    high_confidence_threshold: float,
    action_ttl_minutes: int,
    app_timezone: ZoneInfo,
    reminder_policy: ReminderPolicy | None = None,
    notification_defaults: NotificationDefaults | None = None,
    rescheduling_service: ReschedulingService | None = None,
) -> ManagementIntentOutcome:
    telegram_user = message.from_user
    if telegram_user is None:
        return ManagementIntentOutcome.HANDLED
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        return ManagementIntentOutcome.NOT_FOUND
    if await get_active_draft_for_user(db_session, user.id) is not None:
        await message.answer("Сначала завершите или отмените активный черновик.")
        return ManagementIntentOutcome.AMBIGUOUS
    contextual_id = (
        replied_work_item_id(message) if intent.contextual_reference else None
    )
    if intent.contextual_reference and contextual_id is None:
        await message.answer(
            "Ответьте Reply на карточку нужной записи или укажите её название."
        )
        return ManagementIntentOutcome.AMBIGUOUS
    matches = await find_intent_targets(
        db_session,
        user.id,
        intent,
        contextual_work_item_id=contextual_id,
    )
    if not matches:
        return ManagementIntentOutcome.NOT_FOUND
    if len(matches) > 1:
        if len(matches) > 10:
            await message.answer("Найдено слишком много записей. Уточните название.")
            return ManagementIntentOutcome.AMBIGUOUS
        action_session = await create_action_session(
            db_session,
            user_id=user.id,
            action=WorkItemAction.SELECT_RECORD,
            ttl_minutes=action_ttl_minutes,
            context={
                "intent": intent.model_dump(mode="json"),
                "candidate_ids": [str(item.id) for item in matches],
            },
            telegram_update_id=event_update.update_id,
        )
        entries = await enrich_work_item_list(db_session, user.id, matches)
        await db_session.commit()
        body = "\n\n".join(
            format_selection_entry(value, index, app_timezone)
            for index, value in enumerate(entries, start=1)
        )
        await message.answer(
            f"Выберите запись:\n\n{body}",
            parse_mode=None,
            reply_markup=selection_keyboard(action_session.id, entries),
        )
        return ManagementIntentOutcome.AMBIGUOUS
    item = matches[0]
    if intent.confidence < high_confidence_threshold or intent.ambiguities:
        await message.answer(
            "Нужно уточнить действие. Откройте запись и выберите кнопку.",
            reply_markup=item_keyboard(item),
        )
        return ManagementIntentOutcome.AMBIGUOUS
    await apply_management_intent(
        message,
        event_update,
        db_session,
        user_id=user.id,
        telegram_user_id=telegram_user.id,
        item=item,
        intent=intent,
        action_ttl_minutes=action_ttl_minutes,
        app_timezone=app_timezone,
        reminder_policy=reminder_policy,
        notification_defaults=notification_defaults,
        rescheduling_service=rescheduling_service,
    )
    return ManagementIntentOutcome.HANDLED
