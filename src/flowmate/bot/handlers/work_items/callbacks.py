# ruff: noqa: RUF001
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from aiogram.types import (
    CallbackQuery,
    Message,
    Update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.callback_data import (
    decode_revision as decode_revision,
)
from flowmate.bot.callback_data import (
    encode_revision as encode_revision,
)
from flowmate.bot.callback_feedback import CallbackFeedback
from flowmate.db.drafts import get_active_draft_for_user
from flowmate.db.users import get_user_by_telegram_id
from flowmate.reminders.actions import (
    StaleReminderError,
    get_reminder_action_target,
    reminder_revision,
)
from flowmate.reminders.parsing import SnoozeParsingService
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.sync import ReminderPolicy
from flowmate.task_engine.action_sessions import (
    get_active_action_session,
)
from flowmate.task_engine.details import get_work_item_details
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    StaleWorkItemError,
    work_item_revision,
)
from flowmate.task_engine.rescheduling import (
    LaterTodayUnavailableError,
    ReschedulePreset,
    ReschedulingService,
)
from flowmate.task_engine.service import (
    get_work_item,
    list_work_item_events,
)
from flowmate.workspaces import activate_workspace

from .cards import parse_work_item_callback, refresh_work_item_card, send_details
from .dates import DATE_ACTIONS, execute_date_action, reschedule_options_keyboard
from .editing import EDIT_ACTIONS, handle_edit_callback, start_input_session
from .lifecycle import LIFECYCLE_ACTIONS, execute_lifecycle_action
from .reminders import (
    REMINDER_ACTIONS,
    execute_reminder_action,
    snooze_options_keyboard,
)

logger = logging.getLogger(__name__)


EVENT_LABELS = {
    "created": "создано",
    "updated": "обновлено",
    "status_changed": "изменён статус",
    "linked": "добавлена связь",
    "completed": "завершено",
    "reopened": "возобновлено",
    "cancelled": "отменено",
    "rescheduled": "перенесено",
    "note_added": "добавлена заметка",
    "topic_changed": "изменена тема",
    "person_changed": "изменён человек",
    "waiting_received": "получен результат",
    "person_replied": "получен ответ",
    "reminder_snoozed": "напоминание отложено",
    "archived": "перенесено в архив",
}


async def work_item_callback(
    callback_query: CallbackQuery,
    event_update: Update,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
    work_item_action_ttl_minutes: int,
    notification_defaults: NotificationDefaults,
    reminder_policy: ReminderPolicy | None = None,
    rescheduling_service: ReschedulingService | None = None,
) -> None:
    feedback = CallbackFeedback(callback_query)
    parsed = parse_work_item_callback(callback_query.data)
    message = callback_query.message
    telegram_user = callback_query.from_user
    if parsed is None or not isinstance(message, Message):
        await feedback.error("Действие недоступно.")
        return
    await feedback.acknowledge()
    action, target_id, argument, callback_workspace = parsed
    action = {"details": "d", "history": "h"}.get(action, action)
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await feedback.error("Запись не найдена.")
        return
    if callback_workspace is not None:
        activate_workspace(
            db_session,
            user_id=user.id,
            workspace=callback_workspace,
        )
    item = await get_work_item(db_session, user.id, target_id)
    if action.startswith("z"):
        reminder_target = await get_reminder_action_target(
            db_session, user.id, target_id
        )
        item = reminder_target.work_item if reminder_target is not None else None
    if item is None:
        await message.edit_text(
            "⚠️ Запись больше недоступна.",
            parse_mode=None,
            reply_markup=None,
        )
        return
    try:
        if action in {"d", "b"}:
            await send_details(
                message,
                db_session,
                user.id,
                item,
                app_timezone,
                edit=action == "b",
            )
            return
        if action == "h":
            events = await list_work_item_events(db_session, user.id, item.id)
            lines = ["История:"] + [
                f"• {event.created_at.astimezone(app_timezone):%d.%m.%Y %H:%M} — "
                f"{EVENT_LABELS[event.event_type]}"
                for event in events
            ]
            await message.answer("\n".join(lines), parse_mode=None)
            await feedback.success("История открыта.")
            return
        if await get_active_draft_for_user(db_session, user.id) is not None:
            await feedback.error("Сначала завершите или отмените активный черновик.")
            return
        if await get_active_action_session(db_session, user.id) is not None:
            await feedback.error("Сначала завершите или отмените текущее действие.")
            return
        expected_revision = decode_revision(argument) if argument is not None else None
        if expected_revision is None:
            await feedback.error("Карточка устарела. Откройте запись заново.")
            return
        if action in EDIT_ACTIONS:
            await handle_edit_callback(
                message,
                event_update,
                db_session,
                feedback,
                action=action,
                user_id=user.id,
                item=item,
                expected_revision=expected_revision,
                action_ttl_minutes=work_item_action_ttl_minutes,
                app_timezone=app_timezone,
            )
            return
        if action == "s":
            details = await get_work_item_details(db_session, user.id, item.id)
            keyboard = snooze_options_keyboard(details) if details is not None else None
            if keyboard is None:
                await feedback.error("Для записи нет активного напоминания.")
                return
            if work_item_revision(item.updated_at) != expected_revision:
                raise StaleWorkItemError("work item card is stale")
            await message.edit_reply_markup(reply_markup=keyboard)
            return
        if action == "r":
            if work_item_revision(item.updated_at) != expected_revision:
                raise StaleWorkItemError("work item card is stale")
            preferences = await get_effective_notification_preferences(
                db_session, user.id, notification_defaults
            )
            current = datetime.now(UTC)
            service = rescheduling_service or ReschedulingService(
                SnoozeParsingService(None, timeout_seconds=1)
            )
            try:
                service.resolve_preset(
                    item,
                    ReschedulePreset.LATER_TODAY,
                    preferences=preferences,
                    now=current,
                )
                later_today_available = True
            except LaterTodayUnavailableError:
                later_today_available = False
            await message.edit_reply_markup(
                reply_markup=reschedule_options_keyboard(
                    item,
                    current.astimezone(app_timezone),
                    later_today_available=later_today_available,
                )
            )
            return
        if action in {"n", "rd", "zi"}:
            if action == "zi":
                if (
                    reminder_target is None
                    or reminder_revision(reminder_target.reminder) != expected_revision
                ):
                    raise StaleReminderError("reminder action is stale")
            elif work_item_revision(item.updated_at) != expected_revision:
                raise StaleWorkItemError("work item card is stale")
            session_action = (
                WorkItemAction.ADD_NOTE
                if action == "n"
                else WorkItemAction.RESCHEDULE
                if action == "rd"
                else WorkItemAction.REMINDER_SNOOZE
            )
            prompt = (
                "Введите текст заметки. Для отмены используйте /cancel."
                if action == "n"
                else (
                    "Введите дату и время или отправьте голосом. "
                    "Для отмены используйте /cancel."
                )
            )
            context: dict[str, object] = {
                "origin_chat_id": message.chat.id,
                "origin_message_id": message.message_id,
                "work_item_revision": work_item_revision(item.updated_at),
            }
            if action == "zi":
                context["reminder_id"] = str(target_id)
                context["reminder_revision"] = expected_revision
            await start_input_session(
                message,
                db_session,
                user_id=user.id,
                item_id=item.id,
                action=session_action,
                prompt=prompt,
                ttl_minutes=work_item_action_ttl_minutes,
                telegram_update_id=event_update.update_id,
                context=context,
            )
            await feedback.prompt("Жду ответ.", remove_keyboard=True)
            return
        mutating_actions = LIFECYCLE_ACTIONS | DATE_ACTIONS | REMINDER_ACTIONS
        if action not in mutating_actions:
            await feedback.error("Действие недоступно.")
            return
        update_id = event_update.update_id
        preferences = await get_effective_notification_preferences(
            db_session, user.id, notification_defaults
        )
        now = datetime.now(UTC)
        if action in LIFECYCLE_ACTIONS:
            changed, response = await execute_lifecycle_action(
                db_session,
                action=action,
                user_id=user.id,
                item=item,
                telegram_update_id=update_id,
                expected_revision=expected_revision,
            )
        elif action in DATE_ACTIONS:
            changed, response = await execute_date_action(
                db_session,
                action=action,
                user_id=user.id,
                item=item,
                telegram_update_id=update_id,
                expected_revision=expected_revision,
                preferences=preferences,
                reminder_policy=reminder_policy,
                rescheduling_service=rescheduling_service,
                now=now,
            )
        else:
            changed, response = await execute_reminder_action(
                db_session,
                action=action,
                user_id=user.id,
                reminder_id=target_id,
                telegram_update_id=update_id,
                expected_revision=expected_revision,
                preferences=preferences,
                now=now,
            )
        await db_session.commit()
        await refresh_work_item_card(
            message,
            db_session,
            user.id,
            item,
            app_timezone,
            notice=(
                f"✅ {response}"
                if changed is None or changed
                else "✅ Действие уже выполнено."
            ),
        )
    except (StaleWorkItemError, StaleReminderError):
        await db_session.rollback()
        await refresh_work_item_card(
            message,
            db_session,
            user.id,
            item,
            app_timezone,
            notice="⚠️ Карточка обновлена. Выберите действие ещё раз.",
        )
    except (InvalidWorkItemTransitionError, ValueError):
        await db_session.rollback()
        await feedback.error("Это действие сейчас недоступно.")
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_work_item_failed user_id=%s operation=%s",
            telegram_user.id,
            action,
        )
        await feedback.error("Не удалось выполнить действие.")
