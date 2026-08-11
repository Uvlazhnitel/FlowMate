# ruff: noqa: RUF001
import logging
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.callback_data import (
    decode_revision as decode_revision,
)
from flowmate.bot.callback_data import (
    encode_revision as encode_revision,
)
from flowmate.bot.callback_feedback import with_callback_status
from flowmate.bot.presentation import (
    TelegramDisplayContext,
    format_due_datetime,
    html_text,
    item_presentation,
    preview,
    status_presentation,
)
from flowmate.db.models import WorkItem
from flowmate.task_engine.details import WorkItemDetails, get_work_item_details
from flowmate.task_engine.enums import WorkItemStatus, WorkItemType
from flowmate.task_engine.management import (
    work_item_revision,
)
from flowmate.workspaces import Workspace

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


def format_datetime(value: datetime | None, timezone: ZoneInfo) -> str:
    return format_due_datetime(value, TelegramDisplayContext(timezone=timezone))


def card_preview(value: str, limit: int) -> str:
    return preview(value, limit)


def format_work_item_details(details: WorkItemDetails, timezone: ZoneInfo) -> str:
    item = details.item
    type_icon, type_label = item_presentation(item.type)
    status_icon, status_label = status_presentation(item.status)
    lines = [
        f"{type_icon} <b>{type_label}</b>",
        html_text(card_preview(item.title, 220)),
        "",
        f"{status_icon} {status_label}",
    ]
    if item.due_at is not None:
        lines.append(f"📅 Срок: {format_datetime(item.due_at, timezone)}")
    if item.next_follow_up_at is not None:
        lines.append(
            f"🔁 Следующий контакт: {format_datetime(item.next_follow_up_at, timezone)}"
        )
    if item.description:
        lines.extend(["", html_text(card_preview(item.description, 800))])
    if details.notes:
        lines.extend(
            ["", "<b>Заметки</b>"]
            + [
                f"• {
                    html_text(
                        card_preview(
                            note.content or '[транскрипция очищена]',
                            260,
                        )
                    )
                }"
                for note in details.notes
            ]
        )
    if details.events:
        lines.extend(
            ["", "<b>Последние изменения</b>"]
            + [
                f"• {event.created_at.astimezone(timezone):%d.%m.%Y %H:%M} — "
                f"{EVENT_LABELS[event.event_type]}"
                for event in details.events
            ]
        )
    return "\n".join(lines)


WORKSPACE_CALLBACK_CODES = {
    Workspace.PERSONAL.value: "p",
    Workspace.WORK.value: "w",
}
CALLBACK_CODE_WORKSPACES = {
    code: workspace for workspace, code in WORKSPACE_CALLBACK_CODES.items()
}


def work_item_callback_data(
    action: str,
    target_id: UUID,
    *,
    argument: str | None = None,
    workspace: str | None = None,
) -> str:
    parts = ["wi", action, str(target_id)]
    if argument is not None:
        parts.append(argument)
    if workspace is not None:
        parts.append(WORKSPACE_CALLBACK_CODES[workspace])
    return ":".join(parts)


def item_keyboard(item: WorkItem) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подробнее",
                    callback_data=work_item_callback_data(
                        "details",
                        item.id,
                        workspace=item.workspace,
                    ),
                )
            ]
        ]
    )


def item_action_data(action: str, item: WorkItem) -> str:
    revision = encode_revision(work_item_revision(item.updated_at))
    return work_item_callback_data(
        action,
        item.id,
        argument=revision,
        workspace=item.workspace,
    )


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
                    text="📖 История",
                    callback_data=work_item_callback_data(
                        "h", item.id, workspace=item.workspace
                    ),
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
            [
                InlineKeyboardButton(
                    text="✏️ Изменить", callback_data=item_action_data("e", item)
                ),
                InlineKeyboardButton(
                    text="📖 История",
                    callback_data=work_item_callback_data(
                        "h", item.id, workspace=item.workspace
                    ),
                ),
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📖 История",
                    callback_data=work_item_callback_data(
                        "h", item.id, workspace=item.workspace
                    ),
                )
            ]
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
        await message.answer(f"{heading}\n\nЗдесь пока нет записей.")
        return
    await message.answer(f"<b>{heading}</b> · {len(items)}", parse_mode="HTML")
    for item in items:
        date = item.next_follow_up_at if item.type == "follow_up" else item.due_at
        icon, label = item_presentation(item.type)
        status_icon, status_label = status_presentation(item.status)
        text = "\n".join(
            (
                f"{icon} <b>{label}</b>",
                html_text(card_preview(item.title, 220)),
                "",
                f"{status_icon} {status_label} · {format_datetime(date, timezone)}",
            )
        )
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=item_keyboard(item),
        )


async def send_details(
    message: Message,
    session: AsyncSession,
    user_id: UUID,
    item: WorkItem,
    timezone: ZoneInfo,
    *,
    edit: bool = False,
    notice: str | None = None,
) -> bool:
    details = await get_work_item_details(session, user_id, item.id)
    if details is None:
        if edit:
            await message.edit_text("Запись больше недоступна.", parse_mode=None)
        else:
            await message.answer("Запись больше недоступна.", parse_mode=None)
        return False
    text = format_work_item_details(details, timezone)
    if notice is not None:
        text = with_callback_status(text, notice)
    if edit:
        await message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=details_keyboard(details),
        )
    else:
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=details_keyboard(details),
        )
    return True


async def refresh_work_item_card(
    message: Message,
    session: AsyncSession,
    user_id: UUID,
    item: WorkItem,
    timezone: ZoneInfo,
    *,
    notice: str | None = None,
) -> None:
    try:
        await send_details(
            message,
            session,
            user_id,
            item,
            timezone,
            edit=True,
            notice=notice,
        )
        return
    except TelegramAPIError:
        logger.warning(
            "telegram_work_item_card_edit_failed item_id=%s category=telegram",
            item.id,
        )
    try:
        await send_details(
            message,
            session,
            user_id,
            item,
            timezone,
            notice=notice,
        )
    except TelegramAPIError:
        logger.error(
            "telegram_work_item_card_send_failed item_id=%s category=telegram",
            item.id,
        )


def parse_work_item_callback(
    data: str | None,
) -> tuple[str, UUID, str | None, str | None] | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) not in {3, 4, 5} or parts[0] != "wi":
        return None
    try:
        item_id = UUID(parts[2])
    except ValueError:
        return None
    tail = parts[3:]
    workspace = None
    if tail and tail[-1] in CALLBACK_CODE_WORKSPACES:
        workspace = CALLBACK_CODE_WORKSPACES[tail.pop()]
    if len(tail) > 1:
        return None
    return parts[1], item_id, tail[0] if tail else None, workspace
