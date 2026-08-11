import logging
from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

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
from flowmate.db.models import WorkItem
from flowmate.reminders.parsing import SnoozeParsingService
from flowmate.reminders.preferences import EffectiveNotificationPreferences
from flowmate.reminders.sync import ReminderPolicy
from flowmate.task_engine.management import (
    work_item_revision,
)
from flowmate.task_engine.rescheduling import (
    ReschedulePreset,
    ReschedulingService,
    effective_schedule,
)

from .cards import format_datetime, work_item_callback_data

logger = logging.getLogger(__name__)

DATE_ACTIONS = {"rt", "rm", "rw", "rn"}


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


def reschedule_options_keyboard(
    item: WorkItem,
    now: datetime,
    *,
    later_today_available: bool | None = None,
) -> InlineKeyboardMarkup:
    revision = encode_revision(work_item_revision(item.updated_at))

    def data(action: str) -> str:
        return work_item_callback_data(
            action,
            item.id,
            argument=revision,
            workspace=item.workspace,
        )

    rows: list[list[InlineKeyboardButton]] = []
    local_now = now.astimezone(now.tzinfo)
    show_later_today = (
        (local_now + timedelta(hours=3, minutes=14)).date() == local_now.date()
        if later_today_available is None
        else later_today_available
    )
    if show_later_today:
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
            InlineKeyboardButton(text="Через неделю", callback_data=data("rn")),
            InlineKeyboardButton(text="Другая дата", callback_data=data("rd")),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="Отмена",
                callback_data=work_item_callback_data(
                    "b", item.id, workspace=item.workspace
                ),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def execute_date_action(
    session: AsyncSession,
    *,
    action: str,
    user_id: UUID,
    item: WorkItem,
    telegram_update_id: int,
    expected_revision: int,
    preferences: EffectiveNotificationPreferences,
    reminder_policy: ReminderPolicy | None,
    rescheduling_service: ReschedulingService | None,
    now: datetime,
) -> tuple[bool, str]:
    service = rescheduling_service or ReschedulingService(
        SnoozeParsingService(None, timeout_seconds=1)
    )
    preset = {
        "rt": ReschedulePreset.LATER_TODAY,
        "rm": ReschedulePreset.TOMORROW_MORNING,
        "rw": ReschedulePreset.NEXT_WORKING_DAY,
        "rn": ReschedulePreset.NEXT_WEEK,
    }[action]
    result = await service.reschedule_preset(
        session,
        user_id,
        item.id,
        telegram_update_id,
        preset,
        preferences=preferences,
        reminder_policy=reminder_policy,
        expected_revision=expected_revision,
        now=now,
    )
    new_date = effective_schedule(result.work_item)
    formatted_date = format_datetime(new_date, preferences.zoneinfo)
    return result.changed, f"Перенесено на {formatted_date}."
