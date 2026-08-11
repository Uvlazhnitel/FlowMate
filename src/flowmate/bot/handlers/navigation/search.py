# ruff: noqa: RUF001
import shlex
from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from aiogram.types import (
    CallbackQuery,
    ForceReply,
    Message,
    Update,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import SearchIntent
from flowmate.bot.callback_feedback import CallbackFeedback
from flowmate.bot.handlers.navigation.lists import (
    _work_item_page,
    send_navigation_page,
)
from flowmate.bot.handlers.navigation.presentation import (
    ExpiredListError,
    NavigationPage,
    parse_search_callback,
)
from flowmate.bot.handlers.work_items.cards import send_details
from flowmate.bot.menu import restore_main_menu
from flowmate.db.models import WorkItemActionSession
from flowmate.db.users import get_user_by_telegram_id
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.action_sessions import (
    create_action_session,
    finish_action_session,
    get_search_session_for_user,
)
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.queries import (
    PersonScope,
)
from flowmate.task_engine.search import (
    ALL_SEARCH_STATUSES,
    ALL_SEARCH_TYPES,
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
        await cancel_transient_dialogs(db_session, user.id)
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
    await restore_main_menu(message)


async def search_callback(
    callback_query: CallbackQuery,
    db_session: AsyncSession,
    app_timezone: ZoneInfo,
) -> None:
    feedback = CallbackFeedback(callback_query)
    parsed = parse_search_callback(callback_query.data)
    message = callback_query.message
    if parsed is None or not isinstance(message, Message):
        await feedback.error(EXPIRED_LIST_MESSAGE)
        return
    await feedback.acknowledge("⏳ Открываю…")
    user = await get_user_by_telegram_id(db_session, callback_query.from_user.id)
    if user is None:
        await feedback.error(EXPIRED_LIST_MESSAGE)
        return
    session_id, page = parsed
    action_session = await get_search_session_for_user(db_session, user.id, session_id)
    if action_session is None:
        await feedback.error(EXPIRED_LIST_MESSAGE)
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
    except ExpiredListError:
        await feedback.error(EXPIRED_LIST_MESSAGE)
    except SQLAlchemyError:
        await db_session.rollback()
        await feedback.error(LIST_FAILED_MESSAGE)
