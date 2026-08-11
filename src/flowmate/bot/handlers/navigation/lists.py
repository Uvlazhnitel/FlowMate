# ruff: noqa: RUF001
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import (
    CallbackQuery,
    Message,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.callback_feedback import CallbackFeedback
from flowmate.bot.formatting import split_plain_text
from flowmate.bot.handlers.navigation.presentation import (
    ExpiredListError,
    NavigationPage,
    format_person_entry,
    format_stale_contact_entry,
    format_topic_entry,
    format_work_item_entry,
    list_keyboard,
    parse_list_callback,
)
from flowmate.db.users import get_user_by_telegram_id
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.queries import (
    PersonScope,
    enrich_work_item_list,
    list_follow_ups,
    list_open_questions,
    list_person_counts,
    list_recent_tasks,
    list_scheduled_items,
    list_today_items,
    list_topic_counts,
    list_waiting_items,
)
from flowmate.task_engine.search import (
    WorkItemSearchFilters,
    search_stale_contacts,
    search_work_items,
)
from flowmate.task_engine.transient_dialogs import cancel_transient_dialogs

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
        start = resolve_local_datetime(now.date(), time.min, timezone)
        items = await list_today_items(
            session,
            user_id,
            start=start,
            end=resolve_local_datetime(
                now.date() + timedelta(days=1), time.min, timezone
            ),
            limit=limit,
            offset=offset,
        )
    elif view == "n":
        tomorrow = now.date() + timedelta(days=1)
        items = await list_scheduled_items(
            session,
            user_id,
            start=resolve_local_datetime(tomorrow, time.min, timezone),
            end=resolve_local_datetime(
                tomorrow + timedelta(days=1), time.min, timezone
            ),
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
            else ("На завтра записей нет." if view == "n" else "Записей нет.")
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
    notification_defaults: NotificationDefaults | None = None,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    try:
        user = await get_user_by_telegram_id(db_session, telegram_user.id)
        if user is None:
            await message.answer("Сначала используйте /start.")
            return
        await cancel_transient_dialogs(db_session, user.id)
        await db_session.commit()
        timezone = app_timezone
        if notification_defaults is not None:
            preferences = await get_effective_notification_preferences(
                db_session, user.id, notification_defaults
            )
            timezone = preferences.zoneinfo
        value = await build_navigation_page(
            db_session,
            user.id,
            view=view,
            page=page,
            timezone=timezone,
        )
        await send_navigation_page(message, value)
    except SQLAlchemyError:
        await db_session.rollback()
        await message.answer(LIST_FAILED_MESSAGE)


async def today_command(
    message: Message, db_session: AsyncSession, app_timezone: ZoneInfo
) -> None:
    await show_list_view(message, db_session, app_timezone, view="d")


async def tomorrow_command(
    message: Message,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
    notification_defaults: NotificationDefaults,
) -> None:
    await show_list_view(
        message,
        db_session,
        app_timezone,
        view="n",
        notification_defaults=notification_defaults,
    )


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


async def list_callback(
    callback_query: CallbackQuery,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
    notification_defaults: NotificationDefaults | None = None,
) -> None:
    feedback = CallbackFeedback(callback_query)
    parsed = parse_list_callback(callback_query.data)
    message = callback_query.message
    if parsed is None or not isinstance(message, Message):
        await feedback.error(EXPIRED_LIST_MESSAGE)
        return
    await feedback.acknowledge("⏳ Открываю…")
    user = await get_user_by_telegram_id(db_session, callback_query.from_user.id)
    if user is None:
        await feedback.error(EXPIRED_LIST_MESSAGE)
        return
    view, page, people_scope = parsed
    try:
        timezone = app_timezone
        if notification_defaults is not None:
            preferences = await get_effective_notification_preferences(
                db_session, user.id, notification_defaults
            )
            timezone = preferences.zoneinfo
        value = await build_navigation_page(
            db_session,
            user.id,
            view=view,
            page=page,
            timezone=timezone,
            people_scope=people_scope or "work",
        )
        await send_navigation_page(message, value, edit=True)
    except ExpiredListError:
        await feedback.error(EXPIRED_LIST_MESSAGE)
    except SQLAlchemyError:
        await db_session.rollback()
        await feedback.error(LIST_FAILED_MESSAGE)
