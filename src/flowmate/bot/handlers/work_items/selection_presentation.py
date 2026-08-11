from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from flowmate.bot.presentation import item_presentation
from flowmate.task_engine.enums import WorkItemType
from flowmate.task_engine.queries import WorkItemListEntry

from .cards import card_preview, format_datetime


def format_selection_entry(
    value: WorkItemListEntry,
    index: int,
    timezone: ZoneInfo,
) -> str:
    item = value.item
    scheduled = (
        item.next_follow_up_at
        if item.type == WorkItemType.FOLLOW_UP.value
        else item.due_at
    )
    icon, label = item_presentation(item.type)
    lines = [
        f"{index}. {icon} {card_preview(item.title, 120)}",
        f"   {label} · {format_datetime(scheduled, timezone)}",
    ]
    return "\n".join(lines)


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
