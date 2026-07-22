# ruff: noqa: RUF001
import json
import logging
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

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
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.errors import AIError
from flowmate.ai.schemas import ManagementAction, ManagementIntent, TemporalStatus
from flowmate.db.drafts import get_active_draft_for_user
from flowmate.db.models import WorkItem, WorkItemActionSession
from flowmate.db.users import get_user_by_telegram_id
from flowmate.reminders.actions import (
    StaleReminderError,
    get_reminder_action_target,
    reminder_revision,
    snooze_work_item_reminder,
)
from flowmate.reminders.parsing import SnoozeParsingError, SnoozeParsingService
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.sync import ReminderPolicy
from flowmate.reminders.timezone import resolve_local_datetime, tomorrow_at
from flowmate.speech.errors import AudioTooLargeError, SpeechError, SpeechTimeoutError
from flowmate.speech.service import TranscriptionService
from flowmate.task_engine.action_sessions import (
    create_action_session,
    finish_action_session,
    get_action_session_for_user,
    get_active_action_session,
)
from flowmate.task_engine.details import WorkItemDetails, get_work_item_details
from flowmate.task_engine.enums import WorkItemAction, WorkItemStatus, WorkItemType
from flowmate.task_engine.intents import (
    AmbiguousManagementCandidateError,
    find_intent_targets,
    resolve_person_candidate,
    resolve_replaced_person_id,
    resolve_topic_candidate,
)
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    StaleWorkItemError,
    add_work_item_note,
    cancel_work_item,
    change_work_item_person,
    change_work_item_topic,
    complete_work_item,
    create_follow_up_from_waiting,
    mark_follow_up_replied,
    mark_waiting_received,
    reopen_work_item,
    reschedule_work_item,
    work_item_revision,
)
from flowmate.task_engine.queries import WorkItemListEntry, enrich_work_item_list
from flowmate.task_engine.service import (
    get_work_item,
    list_work_item_events,
)

logger = logging.getLogger(__name__)
LIST_FAILED_MESSAGE = "Не удалось загрузить записи. Попробуйте позже."

OPEN_LABELS = {
    WorkItemType.TASK.value: "задача",
    WorkItemType.FOLLOW_UP.value: "follow-up",
    WorkItemType.WAITING.value: "ожидание",
    WorkItemType.QUESTION.value: "вопрос",
    WorkItemType.DECISION.value: "решение",
    WorkItemType.AGENDA_ITEM.value: "повестка",
}
STATUS_LABELS = {
    "inbox": "входящие",
    "planned": "запланировано",
    "active": "в работе",
    "waiting": "ожидание",
    "snoozed": "отложено",
    "done": "завершено",
    "cancelled": "отменено",
    "archived": "архив",
}
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


def format_datetime(value: datetime | None, timezone: ZoneInfo) -> str:
    if value is None:
        return "не задано"
    return value.astimezone(timezone).strftime("%d.%m.%Y %H:%M")


def card_preview(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return (
        normalized
        if len(normalized) <= limit
        else f"{normalized[: limit - 1].rstrip()}…"
    )


def format_work_item_details(details: WorkItemDetails, timezone: ZoneInfo) -> str:
    item = details.item
    people_text = (
        card_preview(", ".join(details.person_names), 240)
        if details.person_names
        else "не указаны"
    )
    topic_text = (
        card_preview(details.topic_name, 120) if details.topic_name else "не задана"
    )
    lines = [
        f"Тип: {OPEN_LABELS[item.type]}",
        f"Название: {card_preview(item.title, 180)}",
        f"Статус: {STATUS_LABELS[item.status]}",
        f"Срок: {format_datetime(item.due_at, timezone)}",
        f"Следующий контакт: {format_datetime(item.next_follow_up_at, timezone)}",
        f"Люди: {people_text}",
        f"Тема: {topic_text}",
    ]
    if item.description:
        lines.append(f"Описание: {card_preview(item.description, 800)}")
    if details.notes:
        lines.extend(
            ["", "Заметки:"]
            + [
                f"• {card_preview(note.content or '[транскрипция очищена]', 260)}"
                for note in details.notes
            ]
        )
    if details.events:
        lines.extend(
            ["", "Последние изменения:"]
            + [
                f"• {event.created_at.astimezone(timezone):%d.%m.%Y %H:%M} — "
                f"{EVENT_LABELS[event.event_type]}"
                for event in details.events
            ]
        )
    return "\n".join(lines)


def item_keyboard(item: WorkItem) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть",
                    callback_data=f"wi:details:{item.id}",
                )
            ]
        ]
    )


def format_selection_entry(
    value: WorkItemListEntry,
    index: int,
    timezone: ZoneInfo,
) -> str:
    item = value.item
    people = card_preview(", ".join(value.person_names), 100) or "—"
    topic = card_preview(value.topic_name, 80) if value.topic_name else "—"
    scheduled = (
        item.next_follow_up_at
        if item.type == WorkItemType.FOLLOW_UP.value
        else item.due_at
    )
    return (
        f"{index}. [{OPEN_LABELS[item.type]}] {card_preview(item.title, 120)}\n"
        f"   Люди: {people}; тема: {topic}; "
        f"дата: {format_datetime(scheduled, timezone)}"
    )


def selection_keyboard(
    action_session_id: UUID,
    entries: list[WorkItemListEntry],
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *[
                [
                    InlineKeyboardButton(
                        text=f"{index}. {card_preview(value.item.title, 42)}",
                        callback_data=f"wis:{action_session_id}:{index - 1}",
                    )
                ]
                for index, value in enumerate(entries, start=1)
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=f"wis:{action_session_id}:x",
                )
            ],
        ]
    )


def encode_revision(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value < 0:
        raise ValueError("revision must not be negative")
    if value == 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result


def decode_revision(value: str) -> int | None:
    try:
        return int(value, 36)
    except ValueError:
        return None


def item_action_data(action: str, item: WorkItem) -> str:
    revision = encode_revision(work_item_revision(item.updated_at))
    return f"wi:{action}:{item.id}:{revision}"


def details_keyboard(details: WorkItemDetails) -> InlineKeyboardMarkup:
    item = details.item
    rows: list[list[InlineKeyboardButton]] = []
    if item.status == WorkItemStatus.DONE.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="↩️ Вернуть", callback_data=item_action_data("o", item)
                ),
                InlineKeyboardButton(
                    text="📖 История", callback_data=f"wi:h:{item.id}"
                ),
            ]
        )
    elif item.status not in {
        WorkItemStatus.CANCELLED.value,
        WorkItemStatus.ARCHIVED.value,
    }:
        if item.type == WorkItemType.WAITING.value:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✅ Получено", callback_data=item_action_data("g", item)
                    ),
                    InlineKeyboardButton(
                        text="🔁 Сделать follow-up",
                        callback_data=item_action_data("f", item),
                    ),
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="⏰ Отложить", callback_data=item_action_data("s", item)
                    ),
                    InlineKeyboardButton(
                        text="📝 Заметка", callback_data=item_action_data("n", item)
                    ),
                ]
            )
        elif item.type == WorkItemType.FOLLOW_UP.value:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✅ Выполнено", callback_data=item_action_data("c", item)
                    ),
                    InlineKeyboardButton(
                        text="💬 Ответ получен",
                        callback_data=item_action_data("a", item),
                    ),
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="⏰ Отложить", callback_data=item_action_data("s", item)
                    ),
                    InlineKeyboardButton(
                        text="📅 Перенести", callback_data=item_action_data("r", item)
                    ),
                    InlineKeyboardButton(
                        text="📝 Заметка", callback_data=item_action_data("n", item)
                    ),
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✅ Выполнено", callback_data=item_action_data("c", item)
                    ),
                    InlineKeyboardButton(
                        text="⏰ Отложить", callback_data=item_action_data("s", item)
                    ),
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text="📅 Перенести", callback_data=item_action_data("r", item)
                    ),
                    InlineKeyboardButton(
                        text="📝 Заметка", callback_data=item_action_data("n", item)
                    ),
                    InlineKeyboardButton(
                        text="❌ Отменить", callback_data=item_action_data("x", item)
                    ),
                ]
            )
        rows.append(
            [InlineKeyboardButton(text="📖 История", callback_data=f"wi:h:{item.id}")]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="📖 История", callback_data=f"wi:h:{item.id}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_item_list(
    message: Message,
    *,
    heading: str,
    items: list[WorkItem],
    timezone: ZoneInfo,
) -> None:
    if not items:
        await message.answer(f"{heading}: записей нет.")
        return
    await message.answer(f"{heading}: {len(items)}")
    for item in items:
        date = item.next_follow_up_at if item.type == "follow_up" else item.due_at
        text = (
            f"[{OPEN_LABELS[item.type]}] {item.title}\n"
            f"Статус: {STATUS_LABELS[item.status]}; "
            f"дата: {format_datetime(date, timezone)}"
        )
        await message.answer(text, parse_mode=None, reply_markup=item_keyboard(item))


async def send_details(
    message: Message,
    session: AsyncSession,
    user_id: UUID,
    item: WorkItem,
    timezone: ZoneInfo,
    *,
    edit: bool = False,
) -> bool:
    details = await get_work_item_details(session, user_id, item.id)
    if details is None:
        if edit:
            await message.edit_text("Запись больше недоступна.", parse_mode=None)
        else:
            await message.answer("Запись больше недоступна.", parse_mode=None)
        return False
    text = format_work_item_details(details, timezone)
    if edit:
        await message.edit_text(
            text,
            parse_mode=None,
            reply_markup=details_keyboard(details),
        )
    else:
        await message.answer(
            text,
            parse_mode=None,
            reply_markup=details_keyboard(details),
        )
    return True


def parse_work_item_callback(data: str | None) -> tuple[str, UUID, str | None] | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) not in {3, 4} or parts[0] != "wi":
        return None
    try:
        item_id = UUID(parts[2])
    except ValueError:
        return None
    return parts[1], item_id, parts[3] if len(parts) == 4 else None


def parse_user_datetime(value: str, timezone: ZoneInfo) -> datetime | None:
    normalized = value.strip()
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(normalized, pattern)
        except ValueError:
            continue
        if "%H" not in pattern:
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return parsed.replace(tzinfo=timezone)
    return None


def snooze_options_keyboard(details: WorkItemDetails) -> InlineKeyboardMarkup | None:
    reminder = details.nearest_reminder
    if reminder is None:
        return None
    revision = encode_revision(reminder_revision(reminder))

    def data(action: str) -> str:
        return f"wi:{action}:{reminder.id}:{revision}"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="15 минут", callback_data=data("z15")),
                InlineKeyboardButton(text="1 час", callback_data=data("z1")),
                InlineKeyboardButton(text="3 часа", callback_data=data("z3")),
            ],
            [
                InlineKeyboardButton(text="Завтра утром", callback_data=data("zt")),
                InlineKeyboardButton(text="По умолчанию", callback_data=data("zd")),
            ],
            [
                InlineKeyboardButton(text="Другая дата", callback_data=data("zi")),
                InlineKeyboardButton(
                    text="Отмена", callback_data=f"wi:b:{details.item.id}"
                ),
            ],
        ]
    )


def reschedule_options_keyboard(item: WorkItem, now: datetime) -> InlineKeyboardMarkup:
    revision = encode_revision(work_item_revision(item.updated_at))

    def data(action: str) -> str:
        return f"wi:{action}:{item.id}:{revision}"

    rows: list[list[InlineKeyboardButton]] = []
    local_now = now.astimezone(now.tzinfo)
    if (local_now + timedelta(hours=3)).date() == local_now.date():
        rows.append(
            [InlineKeyboardButton(text="Позже сегодня", callback_data=data("rt"))]
        )
    rows.append(
        [
            InlineKeyboardButton(text="Завтра утром", callback_data=data("rm")),
            InlineKeyboardButton(
                text="Следующий рабочий день", callback_data=data("rw")
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="Другая дата", callback_data=data("rd")),
            InlineKeyboardButton(text="Отмена", callback_data=f"wi:b:{item.id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def next_working_day(
    now: datetime,
    item: WorkItem,
    timezone: ZoneInfo,
    morning_time: time,
) -> datetime:
    target_date = now.astimezone(timezone).date() + timedelta(days=1)
    while target_date.weekday() >= 5:
        target_date += timedelta(days=1)
    current = (
        item.next_follow_up_at
        if item.type == WorkItemType.FOLLOW_UP.value
        else item.due_at
    )
    target_time = (
        current.astimezone(timezone).time().replace(tzinfo=None)
        if current is not None
        else morning_time
    )
    return resolve_local_datetime(target_date, target_time, timezone).astimezone(UTC)


async def start_input_session(
    message: Message,
    session: AsyncSession,
    *,
    user_id: UUID,
    item_id: UUID,
    action: WorkItemAction,
    prompt: str,
    ttl_minutes: int,
    telegram_update_id: int,
    context: dict[str, object] | None = None,
) -> None:
    action_session = await create_action_session(
        session,
        user_id=user_id,
        action=action,
        ttl_minutes=ttl_minutes,
        work_item_id=item_id,
        context=context,
        telegram_update_id=telegram_update_id,
    )
    if action_session.prompt_message_id is not None:
        await session.rollback()
        await message.answer("Запрос уже обработан.")
        return
    sent = await message.answer(prompt, reply_markup=ForceReply(selective=True))
    action_session.prompt_message_id = sent.message_id
    await session.commit()


async def work_item_callback(
    callback_query: CallbackQuery,
    event_update: Update,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
    work_item_action_ttl_minutes: int,
    notification_defaults: NotificationDefaults,
    reminder_policy: ReminderPolicy | None = None,
) -> None:
    parsed = parse_work_item_callback(callback_query.data)
    message = callback_query.message
    telegram_user = callback_query.from_user
    if parsed is None or not isinstance(message, Message):
        await callback_query.answer("Действие недоступно.")
        return
    action, target_id, argument = parsed
    action = {"details": "d", "history": "h"}.get(action, action)
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await callback_query.answer("Запись не найдена.")
        return
    item = await get_work_item(db_session, user.id, target_id)
    if action.startswith("z"):
        reminder_target = await get_reminder_action_target(
            db_session, user.id, target_id
        )
        item = reminder_target.work_item if reminder_target is not None else None
    if item is None:
        await message.edit_text("Запись больше недоступна.", parse_mode=None)
        await callback_query.answer("Запись больше недоступна.", show_alert=True)
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
            await callback_query.answer()
            return
        if action == "h":
            events = await list_work_item_events(db_session, user.id, item.id)
            lines = ["История:"] + [
                f"• {event.created_at.astimezone(app_timezone):%d.%m.%Y %H:%M} — "
                f"{EVENT_LABELS[event.event_type]}"
                for event in events
            ]
            await message.answer("\n".join(lines), parse_mode=None)
            await callback_query.answer()
            return
        if await get_active_draft_for_user(db_session, user.id) is not None:
            await callback_query.answer(
                "Сначала завершите или отмените активный черновик.", show_alert=True
            )
            return
        if await get_active_action_session(db_session, user.id) is not None:
            await callback_query.answer(
                "Сначала завершите или отмените текущее действие.", show_alert=True
            )
            return
        expected_revision = decode_revision(argument) if argument is not None else None
        if expected_revision is None:
            await callback_query.answer(
                "Карточка устарела. Откройте запись заново.", show_alert=True
            )
            return
        if action == "s":
            details = await get_work_item_details(db_session, user.id, item.id)
            keyboard = snooze_options_keyboard(details) if details is not None else None
            if keyboard is None:
                await callback_query.answer(
                    "Для записи нет активного напоминания.", show_alert=True
                )
                return
            if work_item_revision(item.updated_at) != expected_revision:
                raise StaleWorkItemError("work item card is stale")
            await message.edit_reply_markup(reply_markup=keyboard)
            await callback_query.answer("Выберите время.")
            return
        if action == "r":
            if work_item_revision(item.updated_at) != expected_revision:
                raise StaleWorkItemError("work item card is stale")
            await message.edit_reply_markup(
                reply_markup=reschedule_options_keyboard(
                    item, datetime.now(app_timezone)
                )
            )
            await callback_query.answer("Выберите новую дату.")
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
            await callback_query.answer("Жду ответ.")
            return
        update_id = event_update.update_id
        preferences = await get_effective_notification_preferences(
            db_session, user.id, notification_defaults
        )
        now = datetime.now(UTC)
        if action == "c":
            result = await complete_work_item(
                db_session,
                user.id,
                item.id,
                update_id,
                expected_revision=expected_revision,
            )
            response = "Запись завершена."
        elif action == "x":
            result = await cancel_work_item(
                db_session,
                user.id,
                item.id,
                update_id,
                expected_revision=expected_revision,
            )
            response = "Запись отменена."
        elif action == "o":
            result = await reopen_work_item(
                db_session,
                user.id,
                item.id,
                update_id,
                expected_revision=expected_revision,
            )
            response = "Запись возвращена во входящие."
        elif action == "a":
            result = await mark_follow_up_replied(
                db_session,
                user.id,
                item.id,
                update_id,
                expected_revision=expected_revision,
            )
            response = "Ответ получен, follow-up завершён."
        elif action == "g":
            result = await mark_waiting_received(
                db_session,
                user.id,
                item.id,
                update_id,
                expected_revision=expected_revision,
            )
            response = "Результат получен."
        elif action == "f":
            _, created = await create_follow_up_from_waiting(
                db_session,
                user.id,
                item.id,
                update_id,
                require_received=False,
                expected_revision=expected_revision,
            )
            result = None
            response = "Follow-up создан." if created else "Follow-up уже создан."
        elif action in {"rt", "rm", "rw"}:
            if action == "rt":
                new_date = now.astimezone(preferences.zoneinfo) + timedelta(hours=3)
                if new_date.date() != now.astimezone(preferences.zoneinfo).date():
                    raise ValueError("later today is no longer available")
            elif action == "rm":
                new_date = tomorrow_at(
                    now,
                    timezone=preferences.zoneinfo,
                    local_time=preferences.morning_digest_time,
                )
            else:
                new_date = next_working_day(
                    now,
                    item,
                    preferences.zoneinfo,
                    preferences.morning_digest_time,
                )
            result = await reschedule_work_item(
                db_session,
                user.id,
                item.id,
                update_id,
                new_date,
                reminder_policy=reminder_policy,
                expected_revision=expected_revision,
            )
            response = (
                f"Перенесено на {format_datetime(new_date, preferences.zoneinfo)}."
            )
        elif action in {"z15", "z1", "z3", "zt", "zd"}:
            duration = {
                "z15": timedelta(minutes=15),
                "z1": timedelta(hours=1),
                "z3": timedelta(hours=3),
                "zd": timedelta(minutes=preferences.default_snooze_minutes),
            }.get(action)
            until = (
                tomorrow_at(
                    now,
                    timezone=preferences.zoneinfo,
                    local_time=preferences.morning_digest_time,
                )
                if action == "zt"
                else None
            )
            _, changed = await snooze_work_item_reminder(
                db_session,
                user.id,
                target_id,
                update_id,
                duration=duration,
                until=until,
                expected_revision=expected_revision,
            )
            result = None
            response = "Напоминание отложено." if changed else "Уже выполнено."
        else:
            await callback_query.answer("Действие недоступно.")
            return
        await db_session.commit()
        await send_details(message, db_session, user.id, item, app_timezone, edit=True)
        await callback_query.answer(
            response if result is None or result.changed else "Действие уже выполнено."
        )
    except (StaleWorkItemError, StaleReminderError):
        await db_session.rollback()
        await send_details(message, db_session, user.id, item, app_timezone, edit=True)
        await callback_query.answer("Карточка обновлена.", show_alert=True)
    except (InvalidWorkItemTransitionError, ValueError):
        await db_session.rollback()
        await callback_query.answer("Это действие сейчас недоступно.", show_alert=True)
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_work_item_failed user_id=%s operation=%s",
            telegram_user.id,
            action,
        )
        await callback_query.answer("Не удалось выполнить действие.", show_alert=True)


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
            from flowmate.bot.handlers.navigation import complete_search_action

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
                "reminder_snooze_voice_failed user_id=%s category=transcription",
                message.from_user.id if message.from_user else 0,
            )
            await message.answer("Не удалось распознать дату. Попробуйте текстом.")
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
            parsed_date = parse_user_datetime(text, preferences.zoneinfo)
            if parsed_date is None:
                parsed_date = await snooze_parsing_service.parse(
                    text,
                    timezone=preferences.zoneinfo,
                    now=datetime.now(preferences.zoneinfo),
                )
            new_date = parsed_date
            await reschedule_work_item(
                db_session,
                action_user_id,
                item_id,
                event_update.update_id,
                new_date,
                reminder_policy=reminder_policy,
                expected_revision=(
                    int(active_work_item_action.context["work_item_revision"])
                    if "work_item_revision" in active_work_item_action.context
                    else None
                ),
            )
            response = (
                f"Запись перенесена на "
                f"{format_datetime(new_date, preferences.zoneinfo)}."
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
                        parse_mode=None,
                        reply_markup=details_keyboard(details),
                    )
                except TelegramAPIError:
                    logger.warning(
                        "telegram_work_item_card_refresh_failed user_id=%s",
                        message.from_user.id if message.from_user else 0,
                    )
        await message.answer(response)
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
        await message.answer("Не удалось применить изменение.")
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_work_item_input_failed user_id=%s action=%s",
            message.from_user.id if message.from_user else 0,
            action.value,
        )
        await message.answer("Не удалось сохранить изменение. Попробуйте позже.")


def replied_work_item_id(message: Message) -> UUID | None:
    replied = message.reply_to_message
    if replied is None or replied.reply_markup is None:
        return None
    for row in replied.reply_markup.inline_keyboard:
        for button in row:
            parsed = parse_work_item_callback(button.callback_data)
            if parsed is not None and parsed[0] == "details":
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
) -> None:
    update_id = event_update.update_id
    try:
        if intent.action is ManagementAction.COMPLETE:
            await complete_work_item(
                db_session, user_id, item.id, telegram_update_id=update_id
            )
            response = "Запись завершена."
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
                        "На какую дату перенести? Введите ГГГГ-ММ-ДД или ДД.ММ.ГГГГ."
                    ),
                    ttl_minutes=action_ttl_minutes,
                    telegram_update_id=update_id,
                )
                return
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
) -> bool:
    telegram_user = message.from_user
    if telegram_user is None:
        return True
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await message.answer("Запись не найдена.")
        return True
    if await get_active_draft_for_user(db_session, user.id) is not None:
        await message.answer("Сначала завершите или отмените активный черновик.")
        return True
    contextual_id = (
        replied_work_item_id(message) if intent.contextual_reference else None
    )
    matches = await find_intent_targets(
        db_session,
        user.id,
        intent,
        contextual_work_item_id=contextual_id,
    )
    if not matches:
        await message.answer("Подходящая запись не найдена.")
        return True
    if len(matches) > 1:
        if len(matches) > 10:
            await message.answer("Найдено слишком много записей. Уточните название.")
            return True
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
        return True
    item = matches[0]
    if intent.confidence < high_confidence_threshold or intent.ambiguities:
        await message.answer(
            "Нужно уточнить действие. Откройте запись и выберите кнопку.",
            reply_markup=item_keyboard(item),
        )
        return True
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
    )
    return True


async def work_item_selection_callback(
    callback_query: CallbackQuery,
    event_update: Update,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
    work_item_action_ttl_minutes: int,
    reminder_policy: ReminderPolicy | None = None,
) -> None:
    message = callback_query.message
    parts = callback_query.data.split(":") if callback_query.data else []
    if len(parts) != 3 or parts[0] != "wis" or not isinstance(message, Message):
        await callback_query.answer("Выбор недоступен.")
        return
    try:
        action_session_id = UUID(parts[1])
    except ValueError:
        await callback_query.answer("Выбор недоступен.")
        return
    user = await get_user_by_telegram_id(db_session, callback_query.from_user.id)
    if user is None:
        await callback_query.answer("Выбор недоступен.")
        return
    action_session = await get_action_session_for_user(
        db_session,
        user.id,
        action_session_id,
        for_update=True,
    )
    if action_session is None or action_session.action != WorkItemAction.SELECT_RECORD:
        await db_session.rollback()
        await callback_query.answer("Срок выбора истёк.", show_alert=True)
        return
    if parts[2] == "x":
        await finish_action_session(db_session, action_session, status="cancelled")
        await db_session.commit()
        await message.edit_text("Выбор отменён.", parse_mode=None)
        await callback_query.answer("Выбор отменён.")
        return
    try:
        index = int(parts[2])
    except ValueError:
        await db_session.rollback()
        await callback_query.answer("Выбор недоступен.")
        return
    candidate_ids = action_session.context.get("candidate_ids")
    intent_payload = action_session.context.get("intent")
    if not isinstance(candidate_ids, list) or not isinstance(intent_payload, dict):
        await db_session.rollback()
        await callback_query.answer("Выбор недоступен.")
        return
    try:
        item_id = UUID(str(candidate_ids[index]))
        intent = ManagementIntent.model_validate_json(json.dumps(intent_payload))
    except (IndexError, ValueError):
        await db_session.rollback()
        await callback_query.answer("Выбор недоступен.")
        return
    item = await get_work_item(db_session, user.id, item_id)
    if item is None:
        await db_session.rollback()
        await callback_query.answer("Запись не найдена.")
        return
    await finish_action_session(db_session, action_session)
    await db_session.flush()
    await apply_management_intent(
        message,
        event_update,
        db_session,
        user_id=user.id,
        telegram_user_id=callback_query.from_user.id,
        item=item,
        intent=intent,
        action_ttl_minutes=work_item_action_ttl_minutes,
        app_timezone=app_timezone,
        reminder_policy=reminder_policy,
    )
    await callback_query.answer()
