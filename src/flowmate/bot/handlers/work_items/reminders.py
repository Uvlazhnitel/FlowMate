import logging
from datetime import datetime, timedelta
from uuid import UUID

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.callback_data import (
    decode_revision as decode_revision,
)
from flowmate.bot.callback_data import (
    encode_revision as encode_revision,
)
from flowmate.reminders.actions import (
    reminder_revision,
    snooze_work_item_reminder,
)
from flowmate.reminders.preferences import EffectiveNotificationPreferences
from flowmate.reminders.timezone import tomorrow_at
from flowmate.task_engine.details import WorkItemDetails

from .cards import work_item_callback_data

logger = logging.getLogger(__name__)

REMINDER_ACTIONS = {"z15", "z1", "z3", "zt", "zd"}


def snooze_options_keyboard(details: WorkItemDetails) -> InlineKeyboardMarkup | None:
    reminder = details.nearest_reminder
    if reminder is None:
        return None
    revision = encode_revision(reminder_revision(reminder))

    def data(action: str) -> str:
        return work_item_callback_data(
            action,
            reminder.id,
            argument=revision,
            workspace=details.item.workspace,
        )

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
                    text="Отмена",
                    callback_data=work_item_callback_data(
                        "b",
                        details.item.id,
                        workspace=details.item.workspace,
                    ),
                ),
            ],
        ]
    )


async def execute_reminder_action(
    session: AsyncSession,
    *,
    action: str,
    user_id: UUID,
    reminder_id: UUID,
    telegram_update_id: int,
    expected_revision: int,
    preferences: EffectiveNotificationPreferences,
    now: datetime,
) -> tuple[None, str]:
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
            local_time=preferences.default_reminder_time,
        )
        if action == "zt"
        else None
    )
    _, changed = await snooze_work_item_reminder(
        session,
        user_id,
        reminder_id,
        telegram_update_id,
        duration=duration,
        until=until,
        expected_revision=expected_revision,
    )
    return None, "Напоминание отложено." if changed else "Уже выполнено."
