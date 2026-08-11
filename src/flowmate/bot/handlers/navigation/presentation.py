# ruff: noqa: RUF001
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from flowmate.bot.presentation import (
    TelegramDisplayContext,
    format_due_datetime,
    item_presentation,
    status_presentation,
)
from flowmate.db.models import WorkItem
from flowmate.task_engine.enums import WorkItemStatus, WorkItemType
from flowmate.task_engine.queries import (
    PersonCount,
    PersonScope,
    TopicCount,
    WorkItemListEntry,
)
from flowmate.task_engine.search import (
    StaleContact,
)

PAGE_SIZE = 5
MAX_PAGE = 999
MAX_TITLE_LENGTH = 120
EXPIRED_LIST_MESSAGE = "Список устарел. Откройте его заново."
LIST_FAILED_MESSAGE = "Не удалось загрузить список. Попробуйте позже."

VIEW_HEADINGS = {
    "d": "📅 Просрочено и на сегодня",
    "n": "📆 На завтра",
    "t": "✅ Активные задачи",
    "f": "🔁 Активные follow-up",
    "w": "⏳ Ожидания",
    "q": "❓ Открытые вопросы",
    "p": "👥 Люди",
    "o": "🗂 Активные темы",
    "s": "🔍 Результаты поиска",
}

PEOPLE_SCOPE_LABELS: dict[PersonScope, str] = {
    "work": "В работе",
    "recent": "Недавние",
    "all": "Все",
}


class ExpiredListError(ValueError):
    """The requested page or search session is no longer available."""


@dataclass(frozen=True, slots=True)
class NavigationPage:
    text: str
    keyboard: InlineKeyboardMarkup


def normalize_display_text(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def effective_item_date(item: WorkItem) -> datetime | None:
    return (
        item.next_follow_up_at
        if item.type == WorkItemType.FOLLOW_UP.value
        else item.due_at
    )


def format_item_date(
    item: WorkItem,
    *,
    timezone: ZoneInfo,
    now: datetime,
) -> str:
    value = effective_item_date(item)
    if value is None:
        return "Без даты"
    context = TelegramDisplayContext(timezone=timezone)
    formatted = format_due_datetime(value, context, now=now)
    if (
        item.status
        in {
            WorkItemStatus.INBOX.value,
            WorkItemStatus.PLANNED.value,
            WorkItemStatus.ACTIVE.value,
            WorkItemStatus.WAITING.value,
            WorkItemStatus.SNOOZED.value,
        }
        and value < now
    ):
        return f"🔴 Просрочено · {formatted}"
    return formatted


def format_work_item_entry(
    value: WorkItemListEntry,
    index: int,
    *,
    timezone: ZoneInfo,
    now: datetime,
) -> str:
    item = value.item
    type_icon, _ = item_presentation(item.type)
    status_icon, status_label = status_presentation(item.status)
    lines = [
        f"{index}. {type_icon} {normalize_display_text(item.title, MAX_TITLE_LENGTH)}",
        f"   {status_icon} {status_label} · "
        f"{format_item_date(item, timezone=timezone, now=now)}",
    ]
    return "\n".join(lines)


def format_person_entry(value: PersonCount, index: int) -> str:
    display_name = normalize_display_text(
        value.person.display_name,
        MAX_TITLE_LENGTH,
    )
    return (
        f"{index}. {display_name}\n"
        f"   Открытых: {value.open_item_count}; follow-up: {value.follow_up_count}; "
        f"ожидания: {value.waiting_count}; вопросы: {value.question_count}"
    )


def format_topic_entry(value: TopicCount, index: int) -> str:
    return (
        f"{index}. {normalize_display_text(value.topic.name, MAX_TITLE_LENGTH)}\n"
        f"   Открытых записей: {value.open_count}"
    )


def format_stale_contact_entry(
    value: StaleContact,
    index: int,
    *,
    timezone: ZoneInfo,
    now: datetime,
) -> str:
    item = value.work_item
    icon, label = item_presentation(item.type)
    return (
        f"{index}. {normalize_display_text(value.person.display_name, 80)} — "
        f"{normalize_display_text(item.title, MAX_TITLE_LENGTH)}\n"
        f"   {icon} {label} · "
        f"{format_item_date(item, timezone=timezone, now=now)}"
    )


def list_keyboard(
    *,
    view: str,
    page: int,
    has_next: bool,
    item_ids: list[UUID],
    search_session_id: UUID | None = None,
    people_scope: PersonScope | None = None,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{index}. Подробнее",
                callback_data=f"wi:details:{item_id}",
            )
        ]
        for index, item_id in enumerate(item_ids, start=1)
    ]
    if view == "p":
        active_scope = people_scope or "work"
        rows.append(
            [
                InlineKeyboardButton(
                    text=(f"• {label}" if scope == active_scope else label),
                    callback_data=f"ls:p:{scope}:0",
                )
                for scope, label in PEOPLE_SCOPE_LABELS.items()
            ]
        )
    navigation: list[InlineKeyboardButton] = []
    callback_prefix = (
        f"lq:{search_session_id}"
        if search_session_id is not None
        else (f"ls:p:{people_scope or 'work'}" if view == "p" else f"ls:{view}")
    )
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="Назад",
                callback_data=f"{callback_prefix}:{page - 1}",
            )
        )
    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text="Вперёд",
                callback_data=f"{callback_prefix}:{page + 1}",
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append([InlineKeyboardButton(text="Главное меню", callback_data="nav:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_list_callback(
    data: str | None,
) -> tuple[str, int, PersonScope | None] | None:
    if data is None:
        return None
    parts = data.split(":")
    if parts[:1] != ["ls"] or len(parts) not in {3, 4}:
        return None
    if parts[1] not in VIEW_HEADINGS:
        return None
    scope: PersonScope | None = None
    page_part = parts[-1]
    if len(parts) == 4:
        if parts[1] != "p" or parts[2] not in PEOPLE_SCOPE_LABELS:
            return None
        scope = parts[2]
    elif parts[1] == "p":
        scope = "work"
    try:
        page = int(page_part)
    except ValueError:
        return None
    if not 0 <= page <= MAX_PAGE or parts[1] == "s":
        return None
    return parts[1], page, scope


def parse_search_callback(data: str | None) -> tuple[UUID, int] | None:
    if data is None:
        return None
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "lq":
        return None
    try:
        session_id = UUID(parts[1])
        page = int(parts[2])
    except ValueError:
        return None
    if not 0 <= page <= MAX_PAGE:
        return None
    return session_id, page
