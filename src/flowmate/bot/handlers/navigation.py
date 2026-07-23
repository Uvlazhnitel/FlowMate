# ruff: noqa: RUF001
import shlex
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import (
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    Update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import SearchIntent
from flowmate.bot.formatting import split_plain_text
from flowmate.bot.handlers.work_items import OPEN_LABELS, STATUS_LABELS, send_details
from flowmate.db.drafts import get_active_draft_for_user
from flowmate.db.models import WorkItem, WorkItemActionSession
from flowmate.db.users import get_user_by_telegram_id
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.action_sessions import (
    create_action_session,
    finish_action_session,
    get_active_action_session,
    get_search_session_for_user,
)
from flowmate.task_engine.enums import WorkItemAction, WorkItemStatus, WorkItemType
from flowmate.task_engine.queries import (
    PersonCount,
    PersonScope,
    TopicCount,
    WorkItemListEntry,
    enrich_work_item_list,
    list_follow_ups,
    list_open_questions,
    list_person_counts,
    list_recent_tasks,
    list_today_items,
    list_topic_counts,
    list_waiting_items,
)
from flowmate.task_engine.search import (
    ALL_SEARCH_STATUSES,
    ALL_SEARCH_TYPES,
    StaleContact,
    WorkItemSearchFilters,
    search_stale_contacts,
    search_work_items,
)

RECORD_BUTTON = "🎙 Записать"
TODAY_BUTTON = "📅 Сегодня"
TASKS_BUTTON = "✅ Задачи"
FOLLOW_UPS_BUTTON = "🔁 Follow-up"
WAITING_BUTTON = "⏳ Ждём"
QUESTIONS_BUTTON = "❓ Вопросы"
PEOPLE_BUTTON = "👥 Люди"
TOPICS_BUTTON = "🗂 Темы"
SEARCH_BUTTON = "🔍 Поиск"
SETTINGS_BUTTON = "⚙️ Настройки"

PAGE_SIZE = 5
MAX_PAGE = 999
MAX_TITLE_LENGTH = 120
MAX_CONTEXT_LENGTH = 60
EXPIRED_LIST_MESSAGE = "Список устарел. Откройте его заново."
LIST_FAILED_MESSAGE = "Не удалось загрузить список. Попробуйте позже."

VIEW_HEADINGS = {
    "d": "📅 Просрочено и на сегодня",
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


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=RECORD_BUTTON), KeyboardButton(text=TODAY_BUTTON)],
            [KeyboardButton(text=TASKS_BUTTON), KeyboardButton(text=FOLLOW_UPS_BUTTON)],
            [
                KeyboardButton(text=WAITING_BUTTON),
                KeyboardButton(text=QUESTIONS_BUTTON),
            ],
            [KeyboardButton(text=PEOPLE_BUTTON), KeyboardButton(text=TOPICS_BUTTON)],
            [KeyboardButton(text=SEARCH_BUTTON), KeyboardButton(text=SETTINGS_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Отправьте текст или голосовое сообщение",
    )


async def menu_command(message: Message) -> None:
    await message.answer(
        "Главное меню FlowMate.",
        parse_mode=None,
        reply_markup=main_menu_keyboard(),
    )


async def record_prompt(message: Message) -> None:
    await message.answer(
        "Отправьте текст или нажмите микрофон Telegram и запишите голосовое сообщение.",
        parse_mode=None,
        reply_markup=main_menu_keyboard(),
    )


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
        return "без даты"
    localized = value.astimezone(timezone)
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
        return f"просрочено: {localized:%d.%m.%Y %H:%M}"
    if localized.date() == now.astimezone(timezone).date():
        return f"сегодня, {localized:%H:%M}"
    return localized.strftime("%d.%m.%Y %H:%M")


def format_work_item_entry(
    value: WorkItemListEntry,
    index: int,
    *,
    timezone: ZoneInfo,
    now: datetime,
) -> str:
    item = value.item
    people = ", ".join(value.person_names[:2])
    if len(value.person_names) > 2:
        people = f"{people} +{len(value.person_names) - 2}"
    people_text = normalize_display_text(people, MAX_CONTEXT_LENGTH) if people else "—"
    topic_text = (
        normalize_display_text(value.topic_name, MAX_CONTEXT_LENGTH)
        if value.topic_name
        else "—"
    )
    return (
        f"{index}. {normalize_display_text(item.title, MAX_TITLE_LENGTH)}\n"
        f"   Дата: {format_item_date(item, timezone=timezone, now=now)}\n"
        f"   Люди: {people_text}; тема: {topic_text}\n"
        f"   Статус: {STATUS_LABELS[item.status]}; тип: {OPEN_LABELS[item.type]}"
    )


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
    return (
        f"{index}. {normalize_display_text(value.person.display_name, 80)} — "
        f"{normalize_display_text(item.title, MAX_TITLE_LENGTH)}\n"
        f"   {OPEN_LABELS[item.type]}; "
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
                text=f"{index}. Открыть",
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


async def _work_item_page(
    session: AsyncSession,
    user_id: UUID,
    *,
    view: str,
    page: int,
    timezone: ZoneInfo,
    query: str | None = None,
    filters: WorkItemSearchFilters | None = None,
    search_session_id: UUID | None = None,
) -> NavigationPage:
    offset = page * PAGE_SIZE
    limit = PAGE_SIZE + 1
    now = datetime.now(timezone)
    if view == "d":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        items = await list_today_items(
            session,
            user_id,
            start=start,
            end=start + timedelta(days=1),
            limit=limit,
            offset=offset,
        )
    elif view == "t":
        items = await list_recent_tasks(session, user_id, limit=limit, offset=offset)
    elif view == "f":
        items = await list_follow_ups(session, user_id, limit=limit, offset=offset)
    elif view == "w":
        items = await list_waiting_items(session, user_id, limit=limit, offset=offset)
    elif view == "q":
        items = await list_open_questions(session, user_id, limit=limit, offset=offset)
    elif view == "s" and (query is not None or filters is not None):
        search_filters = filters or WorkItemSearchFilters(
            text_query=query,
            include_all_statuses=True,
        )
        if search_filters.stale_contacts:
            contacts = await search_stale_contacts(
                session,
                user_id,
                limit=limit,
                offset=offset,
            )
            has_next = len(contacts) > PAGE_SIZE
            page_contacts = contacts[:PAGE_SIZE]
            if page > 0 and not page_contacts:
                raise ExpiredListError
            body = (
                "\n\n".join(
                    format_stale_contact_entry(
                        value,
                        offset + index,
                        timezone=timezone,
                        now=now,
                    )
                    for index, value in enumerate(page_contacts, start=1)
                )
                if page_contacts
                else "Ничего не найдено. Попробуйте изменить запрос."
            )
            return NavigationPage(
                text=f"{VIEW_HEADINGS[view]} · страница {page + 1}\n\n{body}",
                keyboard=list_keyboard(
                    view=view,
                    page=page,
                    has_next=has_next,
                    item_ids=[value.work_item.id for value in page_contacts],
                    search_session_id=search_session_id,
                ),
            )
        items = await search_work_items(
            session,
            user_id,
            search_filters,
            now=now,
            limit=limit,
            offset=offset,
        )
    else:
        raise ExpiredListError
    has_next = len(items) > PAGE_SIZE
    page_items = items[:PAGE_SIZE]
    if page > 0 and not page_items:
        raise ExpiredListError
    entries = await enrich_work_item_list(session, user_id, page_items)
    heading = VIEW_HEADINGS[view]
    body = (
        "\n\n".join(
            format_work_item_entry(
                value,
                offset + index,
                timezone=timezone,
                now=now,
            )
            for index, value in enumerate(entries, start=1)
        )
        if entries
        else (
            "Ничего не найдено. Попробуйте изменить запрос."
            if view == "s"
            else "Записей нет."
        )
    )
    return NavigationPage(
        text=f"{heading} · страница {page + 1}\n\n{body}",
        keyboard=list_keyboard(
            view=view,
            page=page,
            has_next=has_next,
            item_ids=[item.id for item in page_items],
            search_session_id=search_session_id,
        ),
    )


async def _directory_page(
    session: AsyncSession,
    user_id: UUID,
    *,
    view: str,
    page: int,
    people_scope: PersonScope = "work",
) -> NavigationPage:
    offset = page * PAGE_SIZE
    limit = PAGE_SIZE + 1
    if view == "p":
        people = await list_person_counts(
            session,
            user_id,
            scope=people_scope,
            now=datetime.now(UTC),
            limit=limit,
            offset=offset,
        )
        empty = (
            "Людей с открытой работой пока нет."
            if people_scope == "work"
            else (
                "За последние 90 дней активности не было."
                if people_scope == "recent"
                else "Людей пока нет."
            )
        )
        has_next = len(people) > PAGE_SIZE
        page_people = people[:PAGE_SIZE]
        page_has_values = bool(page_people)
        body = (
            "\n\n".join(
                format_person_entry(value, offset + index)
                for index, value in enumerate(page_people, start=1)
            )
            if page_people
            else empty
        )
    elif view == "o":
        topics = await list_topic_counts(session, user_id, limit=limit, offset=offset)
        empty = "Активных тем нет."
        has_next = len(topics) > PAGE_SIZE
        page_topics = topics[:PAGE_SIZE]
        page_has_values = bool(page_topics)
        body = (
            "\n\n".join(
                format_topic_entry(value, offset + index)
                for index, value in enumerate(page_topics, start=1)
            )
            if page_topics
            else empty
        )
    else:
        raise ExpiredListError
    if page > 0 and not page_has_values:
        raise ExpiredListError
    scope_label = f" · {PEOPLE_SCOPE_LABELS[people_scope]}" if view == "p" else ""
    return NavigationPage(
        text=f"{VIEW_HEADINGS[view]}{scope_label} · страница {page + 1}\n\n{body}",
        keyboard=list_keyboard(
            view=view,
            page=page,
            has_next=has_next,
            item_ids=[],
            people_scope=people_scope if view == "p" else None,
        ),
    )


async def build_navigation_page(
    session: AsyncSession,
    user_id: UUID,
    *,
    view: str,
    page: int,
    timezone: ZoneInfo,
    people_scope: PersonScope = "work",
) -> NavigationPage:
    if view in {"p", "o"}:
        return await _directory_page(
            session,
            user_id,
            view=view,
            page=page,
            people_scope=people_scope,
        )
    return await _work_item_page(
        session,
        user_id,
        view=view,
        page=page,
        timezone=timezone,
    )


async def build_search_page(
    session: AsyncSession,
    user_id: UUID,
    action_session: WorkItemActionSession,
    *,
    page: int,
    timezone: ZoneInfo,
) -> NavigationPage:
    try:
        if "filters" in action_session.context:
            filters = WorkItemSearchFilters.from_context(
                action_session.context.get("filters")
            )
            query = None
        else:
            query_value = action_session.context.get("query")
            if not isinstance(query_value, str) or not query_value:
                raise ValueError("legacy search query is missing")
            query = query_value
            filters = None
    except ValueError as error:
        raise ExpiredListError from error
    value = await _work_item_page(
        session,
        user_id,
        view="s",
        page=page,
        timezone=timezone,
        query=query,
        filters=filters,
        search_session_id=action_session.id,
    )
    return value


async def send_navigation_page(
    message: Message,
    value: NavigationPage,
    *,
    edit: bool = False,
) -> None:
    chunks = split_plain_text(value.text)
    if edit:
        await message.edit_text(
            chunks[0],
            parse_mode=None,
            reply_markup=value.keyboard if len(chunks) == 1 else None,
        )
        for index, chunk in enumerate(chunks[1:], start=1):
            await message.answer(
                chunk,
                parse_mode=None,
                reply_markup=value.keyboard if index == len(chunks) - 1 else None,
            )
        return
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            parse_mode=None,
            reply_markup=value.keyboard if index == len(chunks) - 1 else None,
        )


async def show_list_view(
    message: Message,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
    *,
    view: str,
    page: int = 0,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    try:
        user = await get_user_by_telegram_id(db_session, telegram_user.id)
        if user is None:
            await message.answer("Сначала используйте /start.")
            return
        value = await build_navigation_page(
            db_session,
            user.id,
            view=view,
            page=page,
            timezone=app_timezone,
        )
        await send_navigation_page(message, value)
    except SQLAlchemyError:
        await db_session.rollback()
        await message.answer(LIST_FAILED_MESSAGE)


async def today_command(
    message: Message, db_session: AsyncSession, app_timezone: ZoneInfo
) -> None:
    await show_list_view(message, db_session, app_timezone, view="d")


async def tasks_command(
    message: Message, db_session: AsyncSession, app_timezone: ZoneInfo
) -> None:
    await show_list_view(message, db_session, app_timezone, view="t")


async def followups_command(
    message: Message, db_session: AsyncSession, app_timezone: ZoneInfo
) -> None:
    await show_list_view(message, db_session, app_timezone, view="f")


async def waiting_command(
    message: Message, db_session: AsyncSession, app_timezone: ZoneInfo
) -> None:
    await show_list_view(message, db_session, app_timezone, view="w")


async def questions_command(
    message: Message, db_session: AsyncSession, app_timezone: ZoneInfo
) -> None:
    await show_list_view(message, db_session, app_timezone, view="q")


async def people_command(
    message: Message, db_session: AsyncSession, app_timezone: ZoneInfo
) -> None:
    await show_list_view(message, db_session, app_timezone, view="p")


async def topics_command(
    message: Message, db_session: AsyncSession, app_timezone: ZoneInfo
) -> None:
    await show_list_view(message, db_session, app_timezone, view="o")


def normalize_search_query(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 200:
        raise ValueError("search query must contain between 1 and 200 characters")
    return normalized


def parse_search_expression(value: str, timezone: ZoneInfo) -> WorkItemSearchFilters:
    normalized = normalize_search_query(value)
    try:
        tokens = shlex.split(normalized)
    except ValueError as error:
        raise ValueError("search query contains invalid quoting") from error
    free_text: list[str] = []
    item_types: list[str] = []
    statuses: list[str] = []
    person_query: str | None = None
    topic_query: str | None = None
    due_from: datetime | None = None
    due_to: datetime | None = None
    include_all_statuses = False
    overdue = False
    for token in tokens:
        if token.casefold() == "overdue":
            overdue = True
            continue
        key, separator, raw = token.partition(":")
        if not separator or key not in {
            "type",
            "status",
            "person",
            "topic",
            "from",
            "to",
        }:
            free_text.append(token)
            continue
        if not raw:
            raise ValueError(f"search operator {key} requires a value")
        if key == "type":
            values = [
                item.strip().casefold().replace("-", "_") for item in raw.split(",")
            ]
            if not values or not set(values) <= ALL_SEARCH_TYPES:
                raise ValueError("invalid search type")
            item_types.extend(values)
        elif key == "status":
            values = [item.strip().casefold() for item in raw.split(",")]
            if values == ["all"]:
                include_all_statuses = True
            elif not values or not set(values) <= ALL_SEARCH_STATUSES:
                raise ValueError("invalid search status")
            else:
                statuses.extend(values)
        elif key == "person":
            if person_query is not None:
                raise ValueError("person filter may be provided only once")
            person_query = raw
        elif key == "topic":
            if topic_query is not None:
                raise ValueError("topic filter may be provided only once")
            topic_query = raw
        else:
            try:
                local_date = date.fromisoformat(raw)
            except ValueError as error:
                raise ValueError("search dates must use YYYY-MM-DD") from error
            boundary = resolve_local_datetime(local_date, time.min, timezone)
            if key == "from":
                due_from = boundary
            else:
                due_to = resolve_local_datetime(
                    local_date + timedelta(days=1), time.min, timezone
                )
    if include_all_statuses and statuses:
        raise ValueError("status:all cannot be combined with explicit statuses")
    return WorkItemSearchFilters(
        text_query=" ".join(free_text) or None,
        person_query=person_query,
        topic_query=topic_query,
        item_types=tuple(dict.fromkeys(item_types)),
        statuses=tuple(dict.fromkeys(statuses)),
        include_all_statuses=include_all_statuses,
        due_from=due_from,
        due_to=due_to,
        overdue=overdue,
    )


def filters_from_search_intent(intent: SearchIntent) -> WorkItemSearchFilters:
    return WorkItemSearchFilters(
        text_query=intent.text_query,
        person_query=intent.person_query,
        topic_query=intent.topic_query,
        item_types=tuple(value.value for value in intent.item_types),
        statuses=tuple(value.value for value in intent.statuses),
        include_all_statuses=intent.include_all_statuses,
        due_from=intent.due_from,
        due_to=intent.due_to,
        overdue=intent.overdue,
        stale_contacts=intent.stale_contacts,
    )


async def execute_search_intent(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    intent: SearchIntent,
    *,
    high_confidence_threshold: float,
    action_ttl_minutes: int,
    timezone: ZoneInfo,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    if intent.confidence < high_confidence_threshold or intent.ambiguities:
        await message.answer("Уточните поисковый запрос и попробуйте ещё раз.")
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    if user is None:
        await message.answer("Сначала используйте /start.")
        return
    filters = filters_from_search_intent(intent)
    action_session = await create_action_session(
        db_session,
        user.id,
        action=WorkItemAction.SEARCH,
        ttl_minutes=action_ttl_minutes,
        context={
            "filters": filters.to_context(),
            "processed_update_ids": [event_update.update_id],
            "source": "ai",
        },
        telegram_update_id=event_update.update_id,
    )
    await finish_action_session(db_session, action_session)
    if filters.stale_contacts:
        contacts = await search_stale_contacts(db_session, user.id, limit=2)
        items = [value.work_item for value in contacts]
    else:
        items = await search_work_items(
            db_session,
            user.id,
            filters,
            now=datetime.now(timezone),
            limit=2,
        )
    await db_session.commit()
    if not items:
        await message.answer("Ничего не найдено. Попробуйте изменить запрос.")
    elif len(items) == 1:
        await send_details(
            message,
            db_session,
            user.id,
            items[0],
            timezone,
        )
    else:
        await _send_search_results(
            message,
            db_session,
            user.id,
            action_session,
            timezone=timezone,
        )


async def _send_search_results(
    message: Message,
    session: AsyncSession,
    user_id: UUID,
    action_session: WorkItemActionSession,
    *,
    timezone: ZoneInfo,
) -> None:
    value = await build_search_page(
        session,
        user_id,
        action_session,
        page=0,
        timezone=timezone,
    )
    await send_navigation_page(message, value)


async def search_command(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
    work_item_action_ttl_minutes: int,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    text = message.text or ""
    query = (
        text.partition(" ")[2] if text.split(" ", 1)[0].startswith("/search") else ""
    )
    try:
        user = await get_user_by_telegram_id(db_session, telegram_user.id)
        if user is None:
            await message.answer("Сначала используйте /start.")
            return
        if await get_active_draft_for_user(db_session, user.id) is not None:
            await message.answer("Сначала завершите или отмените активный черновик.")
            return
        active_action = await get_active_action_session(db_session, user.id)
        if active_action is not None:
            await message.answer("Сначала завершите или отмените текущее действие.")
            return
        action_session = await create_action_session(
            db_session,
            user.id,
            action=WorkItemAction.SEARCH,
            ttl_minutes=work_item_action_ttl_minutes,
            context={"processed_update_ids": [event_update.update_id]},
            telegram_update_id=event_update.update_id,
        )
        if action_session.status != "open":
            await _send_search_results(
                message,
                db_session,
                user.id,
                action_session,
                timezone=app_timezone,
            )
            return
        if query:
            filters = parse_search_expression(query, app_timezone)
            action_session.context = {
                **action_session.context,
                "filters": filters.to_context(),
            }
            await finish_action_session(db_session, action_session)
            await db_session.commit()
            await _send_search_results(
                message,
                db_session,
                user.id,
                action_session,
                timezone=app_timezone,
            )
            return
        sent = await message.answer(
            "Что найти среди рабочих записей?",
            reply_markup=ForceReply(selective=True),
        )
        action_session.prompt_message_id = sent.message_id
        await db_session.commit()
    except ValueError:
        await db_session.rollback()
        await message.answer("Введите поисковый запрос длиной до 200 символов.")
    except SQLAlchemyError:
        await db_session.rollback()
        await message.answer(LIST_FAILED_MESSAGE)


async def complete_search_action(
    message: Message,
    db_session: AsyncSession,
    action_session: WorkItemActionSession,
    user_id: UUID,
    *,
    query: str,
    timezone: ZoneInfo,
    telegram_update_id: int,
) -> None:
    processed_ids = action_session.context.get("processed_update_ids", [])
    if not isinstance(processed_ids, list):
        processed_ids = []
    filters = parse_search_expression(query, timezone)
    action_session.context = {
        **action_session.context,
        "filters": filters.to_context(),
        "processed_update_ids": [*processed_ids, telegram_update_id],
    }
    await finish_action_session(db_session, action_session)
    await db_session.commit()
    await _send_search_results(
        message,
        db_session,
        user_id,
        action_session,
        timezone=timezone,
    )


async def list_callback(
    callback_query: CallbackQuery,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
) -> None:
    parsed = parse_list_callback(callback_query.data)
    message = callback_query.message
    if parsed is None or not isinstance(message, Message):
        await callback_query.answer(EXPIRED_LIST_MESSAGE, show_alert=True)
        return
    user = await get_user_by_telegram_id(db_session, callback_query.from_user.id)
    if user is None:
        await callback_query.answer(EXPIRED_LIST_MESSAGE, show_alert=True)
        return
    view, page, people_scope = parsed
    try:
        value = await build_navigation_page(
            db_session,
            user.id,
            view=view,
            page=page,
            timezone=app_timezone,
            people_scope=people_scope or "work",
        )
        await send_navigation_page(message, value, edit=True)
        await callback_query.answer()
    except ExpiredListError:
        await callback_query.answer(EXPIRED_LIST_MESSAGE, show_alert=True)
    except SQLAlchemyError:
        await db_session.rollback()
        await callback_query.answer(LIST_FAILED_MESSAGE, show_alert=True)


async def search_callback(
    callback_query: CallbackQuery,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
) -> None:
    parsed = parse_search_callback(callback_query.data)
    message = callback_query.message
    if parsed is None or not isinstance(message, Message):
        await callback_query.answer(EXPIRED_LIST_MESSAGE, show_alert=True)
        return
    user = await get_user_by_telegram_id(db_session, callback_query.from_user.id)
    if user is None:
        await callback_query.answer(EXPIRED_LIST_MESSAGE, show_alert=True)
        return
    session_id, page = parsed
    action_session = await get_search_session_for_user(db_session, user.id, session_id)
    if action_session is None:
        await callback_query.answer(EXPIRED_LIST_MESSAGE, show_alert=True)
        return
    try:
        value = await build_search_page(
            db_session,
            user.id,
            action_session,
            page=page,
            timezone=app_timezone,
        )
        await send_navigation_page(message, value, edit=True)
        await callback_query.answer()
    except ExpiredListError:
        await callback_query.answer(EXPIRED_LIST_MESSAGE, show_alert=True)
    except SQLAlchemyError:
        await db_session.rollback()
        await callback_query.answer(LIST_FAILED_MESSAGE, show_alert=True)


async def menu_callback(callback_query: CallbackQuery) -> None:
    if callback_query.data != "nav:menu":
        await callback_query.answer("Действие недоступно.")
        return
    message = callback_query.message
    if not isinstance(message, Message):
        await callback_query.answer("Действие недоступно.")
        return
    await menu_command(message)
    await callback_query.answer()
