# ruff: noqa: RUF001
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

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

from flowmate.bot.handlers.work_items import (
    send_details,
    send_item_list,
    start_input_session,
)
from flowmate.db.drafts import get_active_draft_for_user
from flowmate.db.models import Reminder
from flowmate.db.users import get_user_by_telegram_id
from flowmate.reminders.actions import (
    get_reminder_action_target,
    snooze_work_item_reminder,
)
from flowmate.reminders.digests import (
    list_digest_reschedule_items,
    move_digest_items_to_tomorrow,
)
from flowmate.reminders.enums import ReminderType
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.sync import ReminderPolicy
from flowmate.reminders.timezone import resolve_local_datetime, tomorrow_at
from flowmate.task_engine.action_sessions import (
    create_action_session,
    finish_action_session,
    get_action_session_for_user,
)
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    complete_work_item,
    create_follow_up_from_waiting,
    mark_follow_up_replied,
    mark_waiting_received,
)
from flowmate.task_engine.queries import list_today_items
from flowmate.task_engine.service import get_work_item

logger = logging.getLogger(__name__)


def parse_reminder_callback(data: str | None) -> tuple[str, UUID] | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "rem":
        return None
    try:
        reminder_id = UUID(parts[2])
    except ValueError:
        return None
    return parts[1], reminder_id


def snooze_keyboard(reminder_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="15 минут",
                    callback_data=f"rem:snooze15m:{reminder_id}",
                ),
                InlineKeyboardButton(
                    text="1 час",
                    callback_data=f"rem:snooze1h:{reminder_id}",
                ),
                InlineKeyboardButton(
                    text="3 часа",
                    callback_data=f"rem:snooze3h:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Завтра утром",
                    callback_data=f"rem:snoozetomorrow:{reminder_id}",
                ),
                InlineKeyboardButton(
                    text="По умолчанию",
                    callback_data=f"rem:snoozedefault:{reminder_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Другая дата",
                    callback_data=f"rem:snoozecustom:{reminder_id}",
                )
            ],
        ]
    )


async def reminder_callback(
    callback_query: CallbackQuery,
    event_update: Update,
    db_session: AsyncSession,
    work_item_action_ttl_minutes: int,
    notification_defaults: NotificationDefaults | None = None,
) -> None:
    parsed = parse_reminder_callback(callback_query.data)
    message = callback_query.message
    if parsed is None or not isinstance(message, Message):
        await callback_query.answer("Действие недоступно.")
        return
    action, reminder_id = parsed
    telegram_user = callback_query.from_user
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await callback_query.answer("Напоминание недоступно.")
        return
    defaults = notification_defaults or NotificationDefaults(
        timezone="UTC",
        morning_digest_time=datetime.min.time(),
        evening_digest_time=datetime.min.time(),
        quiet_hours_start=datetime.min.time(),
        quiet_hours_end=datetime.max.time().replace(microsecond=0),
        snooze_minutes=60,
    )
    if await get_active_draft_for_user(db_session, user.id) is not None:
        await callback_query.answer(
            "Сначала завершите или отмените активный черновик.",
            show_alert=True,
        )
        return
    target = await get_reminder_action_target(
        db_session,
        user.id,
        reminder_id,
    )
    if target is None:
        await callback_query.answer("Напоминание больше не актуально.")
        return
    update_id = event_update.update_id
    try:
        if action == "snooze":
            await message.answer(
                "На сколько отложить напоминание?",
                reply_markup=snooze_keyboard(reminder_id),
            )
            await callback_query.answer()
            return
        if action == "reschedule":
            await start_input_session(
                message,
                db_session,
                user_id=user.id,
                item_id=target.work_item.id,
                action=WorkItemAction.RESCHEDULE,
                prompt="Введите новую дату: ГГГГ-ММ-ДД или ДД.ММ.ГГГГ.",
                ttl_minutes=work_item_action_ttl_minutes,
                telegram_update_id=update_id,
                context={"reminder_id": str(reminder_id)},
            )
            await callback_query.answer()
            return
        if action == "snoozecustom":
            await start_input_session(
                message,
                db_session,
                user_id=user.id,
                item_id=target.work_item.id,
                action=WorkItemAction.REMINDER_SNOOZE,
                prompt=("Введите дату и время или отправьте голосом, когда напомнить."),
                ttl_minutes=work_item_action_ttl_minutes,
                telegram_update_id=update_id,
                context={"reminder_id": str(reminder_id)},
            )
            await callback_query.answer()
            return
        if action in {
            "snooze15m",
            "snooze1h",
            "snooze3h",
            "snoozetomorrow",
            "snoozedefault",
        }:
            preferences = await get_effective_notification_preferences(
                db_session, user.id, defaults
            )
            duration = {
                "snooze15m": timedelta(minutes=15),
                "snooze1h": timedelta(hours=1),
                "snooze3h": timedelta(hours=3),
                "snoozedefault": timedelta(minutes=preferences.default_snooze_minutes),
            }.get(action)
            until = (
                tomorrow_at(
                    datetime.now(UTC),
                    timezone=preferences.zoneinfo,
                    local_time=preferences.morning_digest_time,
                )
                if action == "snoozetomorrow"
                else None
            )
            _, changed = await snooze_work_item_reminder(
                db_session,
                user.id,
                reminder_id,
                update_id,
                duration=duration,
                until=until,
            )
            response = (
                "Напоминание отложено." if changed else "Это действие уже выполнено."
            )
        elif action == "done":
            result = await complete_work_item(
                db_session,
                user.id,
                target.work_item.id,
                update_id,
            )
            response = (
                "Запись завершена." if result.changed else "Это действие уже выполнено."
            )
        elif action == "replied":
            result = await mark_follow_up_replied(
                db_session,
                user.id,
                target.work_item.id,
                update_id,
            )
            response = (
                "Ответ получен, follow-up завершён."
                if result.changed
                else "Это действие уже выполнено."
            )
        elif action == "received":
            result = await mark_waiting_received(
                db_session,
                user.id,
                target.work_item.id,
                update_id,
            )
            response = (
                "Результат получен, ожидание завершено."
                if result.changed
                else "Это действие уже выполнено."
            )
        elif action == "followup":
            _, created = await create_follow_up_from_waiting(
                db_session,
                user.id,
                target.work_item.id,
                update_id,
                require_received=False,
            )
            response = "Follow-up создан." if created else "Follow-up уже создан."
        else:
            await callback_query.answer("Действие недоступно.")
            return
        await db_session.commit()
        await message.answer(response, parse_mode=None)
        await callback_query.answer()
    except (InvalidWorkItemTransitionError, ValueError):
        await db_session.rollback()
        await callback_query.answer(
            "Это действие сейчас недоступно.",
            show_alert=True,
        )
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_reminder_action_failed user_id=%s action=%s",
            telegram_user.id,
            action,
        )
        await callback_query.answer("Не удалось выполнить действие.", show_alert=True)


def parse_digest_callback(data: str | None) -> tuple[str, UUID] | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "dig":
        return None
    try:
        return parts[1], UUID(parts[2])
    except ValueError:
        return None


def digest_review_keyboard(session_id: UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Следующая",
                    callback_data=f"dig:next:{session_id}",
                ),
                InlineKeyboardButton(
                    text="Завершить обзор",
                    callback_data=f"dig:stop:{session_id}",
                ),
            ]
        ]
    )


async def _send_review_item(
    message: Message,
    session: AsyncSession,
    *,
    user_id: UUID,
    action_session_id: UUID,
    item_id: UUID,
    timezone: ZoneInfo,
) -> None:
    item = await get_work_item(session, user_id, item_id)
    if item is None:
        return
    await send_details(message, session, user_id, item, timezone)
    await message.answer(
        "Продолжить обзор?",
        reply_markup=digest_review_keyboard(action_session_id),
    )


async def digest_callback(
    callback_query: CallbackQuery,
    event_update: Update,
    db_session: AsyncSession,
    notification_defaults: NotificationDefaults,
    work_item_action_ttl_minutes: int,
    reminder_policy: ReminderPolicy | None = None,
) -> None:
    parsed = parse_digest_callback(callback_query.data)
    message = callback_query.message
    if parsed is None or not isinstance(message, Message):
        await callback_query.answer("Действие недоступно.")
        return
    action, object_id = parsed
    telegram_user = callback_query.from_user
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await callback_query.answer("Обзор недоступен.")
        return
    try:
        if action in {"next", "stop"}:
            action_session = await get_action_session_for_user(
                db_session, user.id, object_id, for_update=True
            )
            if (
                action_session is None
                or action_session.action != WorkItemAction.DIGEST_REVIEW.value
            ):
                await callback_query.answer("Обзор уже завершён.")
                return
            processed_update_ids = [
                value
                for value in action_session.context.get("processed_update_ids", [])
                if isinstance(value, int)
            ]
            if event_update.update_id in processed_update_ids:
                await callback_query.answer("Это действие уже выполнено.")
                return
            if action == "stop":
                await finish_action_session(db_session, action_session)
                await db_session.commit()
                await message.answer("Обзор завершён.")
                await callback_query.answer()
                return
            item_ids = [UUID(value) for value in action_session.context["item_ids"]]
            index = int(action_session.context.get("index", 0)) + 1
            if index >= len(item_ids):
                await finish_action_session(db_session, action_session)
                await db_session.commit()
                await message.answer("Все записи просмотрены.")
                await callback_query.answer()
                return
            action_session.context = {
                **action_session.context,
                "index": index,
                "processed_update_ids": [
                    *processed_update_ids,
                    event_update.update_id,
                ][-20:],
            }
            await db_session.commit()
            preferences = await get_effective_notification_preferences(
                db_session, user.id, notification_defaults
            )
            await _send_review_item(
                message,
                db_session,
                user_id=user.id,
                action_session_id=action_session.id,
                item_id=item_ids[index],
                timezone=preferences.zoneinfo,
            )
            await callback_query.answer()
            return

        reminder = await db_session.scalar(
            select(Reminder).where(
                Reminder.id == object_id,
                Reminder.user_id == user.id,
                Reminder.type.in_(
                    [
                        ReminderType.MORNING_DIGEST.value,
                        ReminderType.EVENING_DIGEST.value,
                    ]
                ),
            )
        )
        if reminder is None:
            await callback_query.answer("Обзор недоступен.")
            return
        if await get_active_draft_for_user(db_session, user.id) is not None:
            await callback_query.answer(
                "Сначала завершите или отмените активный черновик.",
                show_alert=True,
            )
            return
        preferences = await get_effective_notification_preferences(
            db_session, user.id, notification_defaults
        )
        local_date = (
            reminder.digest_local_date or datetime.now(preferences.zoneinfo).date()
        )
        local_start = resolve_local_datetime(
            local_date, datetime.min.time(), preferences.zoneinfo
        )
        local_end = resolve_local_datetime(
            local_date + timedelta(days=1),
            datetime.min.time(),
            preferences.zoneinfo,
        )
        if action == "today":
            items = await list_today_items(
                db_session,
                user.id,
                start=local_start,
                end=local_end,
            )
            await send_item_list(
                message,
                heading="Просрочено и на сегодня",
                items=items,
                timezone=preferences.zoneinfo,
            )
        elif action == "tomorrow":
            count = await move_digest_items_to_tomorrow(
                db_session,
                user.id,
                local_date=local_date,
                telegram_update_id=event_update.update_id,
                preferences=preferences,
                reminder_policy=reminder_policy,
            )
            await db_session.commit()
            response = (
                f"На завтра перенесено записей: {count}."
                if count
                else "Переносить нечего."
            )
            await message.answer(response)
        elif action == "review":
            items = await list_digest_reschedule_items(
                db_session,
                user.id,
                local_date=local_date,
                preferences=preferences,
            )
            if not items:
                await message.answer("Для разбора записей нет.")
            else:
                action_session = await create_action_session(
                    db_session,
                    user.id,
                    action=WorkItemAction.DIGEST_REVIEW,
                    ttl_minutes=work_item_action_ttl_minutes,
                    work_item_id=items[0].id,
                    context={
                        "item_ids": [str(item.id) for item in items[:10]],
                        "index": 0,
                    },
                    telegram_update_id=event_update.update_id,
                )
                await db_session.commit()
                await _send_review_item(
                    message,
                    db_session,
                    user_id=user.id,
                    action_session_id=action_session.id,
                    item_id=items[0].id,
                    timezone=preferences.zoneinfo,
                )
        else:
            await callback_query.answer("Действие недоступно.")
            return
        await callback_query.answer()
    except (ValueError, InvalidWorkItemTransitionError, KeyError):
        await db_session.rollback()
        await callback_query.answer("Это действие сейчас недоступно.", show_alert=True)
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_digest_action_failed user_id=%s action=%s",
            telegram_user.id,
            action,
        )
        await callback_query.answer("Не удалось выполнить действие.", show_alert=True)
