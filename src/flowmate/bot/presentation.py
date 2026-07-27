# ruff: noqa: RUF001
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from flowmate.reminders.preferences import EffectiveNotificationPreferences

TELEGRAM_TEXT_LIMIT = 4000

MONTHS = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

ITEM_PRESENTATION: dict[str, tuple[str, str]] = {
    "task": ("📌", "Задача"),
    "follow_up": ("🔁", "Follow-up"),
    "waiting": ("⏳", "Ожидание"),
    "question": ("❓", "Вопрос"),
    "note": ("🗒", "Заметка"),
    "decision": ("💡", "Решение"),
    "agenda_item": ("🗣", "Повестка"),
    "unknown": ("📝", "Запись"),
}

STATUS_PRESENTATION: dict[str, tuple[str, str]] = {
    "inbox": ("📥", "Во входящих"),
    "planned": ("🗓", "Запланировано"),
    "active": ("🟢", "В работе"),
    "waiting": ("⏳", "Ожидаем"),
    "snoozed": ("💤", "Отложено"),
    "done": ("✅", "Выполнено"),
    "cancelled": ("🚫", "Отменено"),
    "archived": ("📦", "В архиве"),
}


@dataclass(frozen=True, slots=True)
class TelegramDisplayContext:
    timezone: ZoneInfo
    date_display_format: str = "day_month_year"
    time_display_format: str = "24h"

    @classmethod
    def from_preferences(
        cls, preferences: EffectiveNotificationPreferences
    ) -> "TelegramDisplayContext":
        return cls(
            timezone=preferences.zoneinfo,
            date_display_format=preferences.date_display_format,
            time_display_format=preferences.time_display_format,
        )


def html_text(value: str) -> str:
    return escape(" ".join(value.split()), quote=False)


def preview(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def item_presentation(item_type: str) -> tuple[str, str]:
    return ITEM_PRESENTATION.get(item_type, ITEM_PRESENTATION["unknown"])


def status_presentation(status: str) -> tuple[str, str]:
    return STATUS_PRESENTATION.get(status, ("•", status.replace("_", " ").title()))


def pluralize(value: int, forms: tuple[str, str, str]) -> str:
    remainder_100 = value % 100
    remainder_10 = value % 10
    if 11 <= remainder_100 <= 14:
        return forms[2]
    if remainder_10 == 1:
        return forms[0]
    if 2 <= remainder_10 <= 4:
        return forms[1]
    return forms[2]


def _format_time(value: datetime, context: TelegramDisplayContext) -> str:
    if context.time_display_format == "12h":
        return value.strftime("%I:%M %p").lstrip("0")
    return value.strftime("%H:%M")


def _format_calendar_date(
    value: datetime,
    context: TelegramDisplayContext,
    *,
    reference: datetime,
) -> str:
    if context.date_display_format == "year_month_day":
        return value.strftime("%Y-%m-%d")
    suffix = f" {value.year}" if value.year != reference.year else ""
    return f"{value.day} {MONTHS[value.month]}{suffix}"


def format_datetime(
    value: datetime | None,
    context: TelegramDisplayContext,
    *,
    now: datetime | None = None,
    date_only: bool = False,
    relative: bool = True,
) -> str:
    if value is None:
        return "Без даты"
    reference = (now or datetime.now(UTC)).astimezone(context.timezone)
    localized = value.astimezone(context.timezone)
    date_label: str
    if relative and localized.date() == reference.date():
        date_label = "Сегодня"
    elif relative and localized.date() == reference.date() + timedelta(days=1):
        date_label = "Завтра"
    elif relative and localized.date() == reference.date() - timedelta(days=1):
        date_label = "Вчера"
    else:
        date_label = _format_calendar_date(localized, context, reference=reference)
    if date_only:
        return date_label
    return f"{date_label}, {_format_time(localized, context)}"


def date_is_effectively_date_only(
    value: datetime | None,
    context: TelegramDisplayContext | None = None,
) -> bool:
    localized = (
        value.astimezone(context.timezone)
        if value is not None and context is not None
        else value
    )
    return bool(
        localized is not None
        and localized.hour == 23
        and localized.minute == 59
        and localized.second == 59
    )


def format_due_datetime(
    value: datetime | None,
    context: TelegramDisplayContext,
    *,
    now: datetime | None = None,
) -> str:
    return format_datetime(
        value,
        context,
        now=now,
        date_only=date_is_effectively_date_only(value, context),
    )


def join_html_blocks(
    blocks: list[str],
    *,
    limit: int = TELEGRAM_TEXT_LIMIT,
) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = block[:limit]
    if current:
        chunks.append(current)
    return chunks
