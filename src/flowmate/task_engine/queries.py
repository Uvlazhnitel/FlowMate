from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from flowmate.db.models import Person, Topic, WorkItem, WorkItemPerson
from flowmate.task_engine.enums import WorkItemStatus, WorkItemType

OPEN_STATUSES = tuple(
    status.value
    for status in (
        WorkItemStatus.INBOX,
        WorkItemStatus.PLANNED,
        WorkItemStatus.ACTIVE,
        WorkItemStatus.WAITING,
        WorkItemStatus.SNOOZED,
    )
)

PersonScope = Literal["work", "recent", "all"]
PEOPLE_RECENT_DAYS = 90


@dataclass(frozen=True, slots=True)
class TopicCount:
    topic: Topic
    open_count: int


@dataclass(frozen=True, slots=True)
class PersonCount:
    person: Person
    open_item_count: int
    follow_up_count: int
    waiting_count: int
    question_count: int
    last_activity: datetime


@dataclass(frozen=True, slots=True)
class WorkItemListEntry:
    item: WorkItem
    topic_name: str | None
    person_names: tuple[str, ...]


def validate_pagination(limit: int, offset: int) -> None:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if offset < 0:
        raise ValueError("offset must not be negative")


def effective_date_expression() -> ColumnElement[datetime]:
    return case(
        (
            WorkItem.type == WorkItemType.FOLLOW_UP.value,
            func.coalesce(WorkItem.next_follow_up_at, WorkItem.due_at),
        ),
        else_=WorkItem.due_at,
    )


async def list_today_items(
    session: AsyncSession,
    user_id: UUID,
    *,
    start: datetime,
    end: datetime,
    limit: int = 10,
    offset: int = 0,
) -> list[WorkItem]:
    validate_pagination(limit, offset)
    effective_date = effective_date_expression()
    statement = (
        select(WorkItem)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.status.in_(OPEN_STATUSES),
            effective_date.is_not(None),
            effective_date < end,
        )
        .order_by(effective_date, WorkItem.created_at, WorkItem.id)
        .offset(offset)
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def list_recent_tasks(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 10,
    offset: int = 0,
) -> list[WorkItem]:
    validate_pagination(limit, offset)
    statement = (
        select(WorkItem)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.type == WorkItemType.TASK.value,
            WorkItem.status.in_(OPEN_STATUSES),
        )
        .order_by(WorkItem.created_at.desc(), WorkItem.id)
        .offset(offset)
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def list_follow_ups(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 10,
    offset: int = 0,
) -> list[WorkItem]:
    validate_pagination(limit, offset)
    statement = (
        select(WorkItem)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.type == WorkItemType.FOLLOW_UP.value,
            WorkItem.status.in_(OPEN_STATUSES),
        )
        .order_by(
            WorkItem.next_follow_up_at.asc().nulls_last(),
            WorkItem.created_at,
            WorkItem.id,
        )
        .offset(offset)
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def list_waiting_items(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 10,
    offset: int = 0,
) -> list[WorkItem]:
    validate_pagination(limit, offset)
    statement = (
        select(WorkItem)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.type == WorkItemType.WAITING.value,
            WorkItem.status.in_(OPEN_STATUSES),
        )
        .order_by(
            WorkItem.due_at.asc().nulls_last(),
            WorkItem.waiting_since.asc().nulls_last(),
            WorkItem.created_at,
            WorkItem.id,
        )
        .offset(offset)
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def list_open_questions(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 10,
    offset: int = 0,
) -> list[WorkItem]:
    validate_pagination(limit, offset)
    statement = (
        select(WorkItem)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.type == WorkItemType.QUESTION.value,
            WorkItem.status.in_(OPEN_STATUSES),
        )
        .order_by(
            WorkItem.due_at.asc().nulls_last(),
            WorkItem.created_at.desc(),
            WorkItem.id,
        )
        .offset(offset)
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def list_topic_counts(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[TopicCount]:
    validate_pagination(limit, offset)
    count = func.count(WorkItem.id).filter(WorkItem.status.in_(OPEN_STATUSES))
    statement = (
        select(Topic, count)
        .outerjoin(
            WorkItem,
            (WorkItem.topic_id == Topic.id) & (WorkItem.user_id == user_id),
        )
        .where(Topic.user_id == user_id, Topic.is_active.is_(True))
        .group_by(Topic.id)
        .order_by(count.desc(), Topic.name, Topic.id)
        .offset(offset)
        .limit(limit)
    )
    return [
        TopicCount(topic, value) for topic, value in await session.execute(statement)
    ]


async def list_person_counts(
    session: AsyncSession,
    user_id: UUID,
    *,
    scope: PersonScope = "work",
    query: str | None = None,
    now: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[PersonCount]:
    validate_pagination(limit, offset)
    if scope not in {"work", "recent", "all"}:
        raise ValueError("invalid people scope")
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("people directory clock must be timezone-aware")
    base_filter = WorkItem.status.in_(OPEN_STATUSES)
    open_items = func.count(WorkItem.id).filter(base_filter)
    follow_ups = func.count(WorkItem.id).filter(
        base_filter, WorkItem.type == WorkItemType.FOLLOW_UP.value
    )
    waiting = func.count(WorkItem.id).filter(
        base_filter, WorkItem.type == WorkItemType.WAITING.value
    )
    questions = func.count(WorkItem.id).filter(
        base_filter, WorkItem.type == WorkItemType.QUESTION.value
    )
    last_work_activity = func.max(WorkItem.updated_at)
    last_activity = func.greatest(
        Person.updated_at,
        func.coalesce(last_work_activity, Person.updated_at),
    )
    statement = (
        select(
            Person,
            open_items,
            follow_ups,
            waiting,
            questions,
            last_activity,
        )
        .outerjoin(
            WorkItemPerson,
            (WorkItemPerson.person_id == Person.id)
            & (WorkItemPerson.user_id == user_id),
        )
        .outerjoin(
            WorkItem,
            (WorkItem.id == WorkItemPerson.work_item_id)
            & (WorkItem.user_id == user_id),
        )
        .where(Person.user_id == user_id, Person.is_active.is_(True))
        .group_by(Person.id)
    )
    normalized_query = query.strip() if query else ""
    if normalized_query:
        statement = statement.where(Person.display_name.ilike(f"%{normalized_query}%"))
    if scope == "work":
        statement = statement.having(open_items > 0)
    elif scope == "recent":
        cutoff = current_time - timedelta(days=PEOPLE_RECENT_DAYS)
        statement = statement.having(
            (open_items > 0)
            | (last_work_activity >= cutoff)
            | (Person.updated_at >= cutoff)
        )
    statement = (
        statement.order_by(
            open_items.desc(),
            last_activity.desc(),
            Person.display_name,
            Person.id,
        )
        .offset(offset)
        .limit(limit)
    )
    rows = await session.execute(statement)
    return [
        PersonCount(
            person,
            open_item_count,
            follow_up_count,
            waiting_count,
            question_count,
            activity,
        )
        for (
            person,
            open_item_count,
            follow_up_count,
            waiting_count,
            question_count,
            activity,
        ) in rows
    ]


async def find_matching_work_items(
    session: AsyncSession,
    user_id: UUID,
    *,
    query: str | None = None,
    item_type: str | None = None,
    person_query: str | None = None,
    topic_query: str | None = None,
    include_completed: bool = False,
    limit: int = 11,
    offset: int = 0,
) -> list[WorkItem]:
    from flowmate.task_engine.search import (
        WorkItemSearchFilters,
        search_work_items,
    )

    return await search_work_items(
        session,
        user_id,
        WorkItemSearchFilters(
            text_query=query,
            person_query=person_query,
            topic_query=topic_query,
            item_types=(item_type,) if item_type is not None else (),
            include_all_statuses=include_completed,
        ),
        now=datetime.now().astimezone(),
        limit=limit,
        offset=offset,
    )


async def enrich_work_item_list(
    session: AsyncSession,
    user_id: UUID,
    items: list[WorkItem],
) -> list[WorkItemListEntry]:
    if not items:
        return []
    topic_ids = {item.topic_id for item in items if item.topic_id is not None}
    topic_names: dict[UUID, str] = {}
    if topic_ids:
        topic_rows = await session.execute(
            select(Topic.id, Topic.name).where(
                Topic.user_id == user_id,
                Topic.id.in_(topic_ids),
            )
        )
        topic_names = {topic_id: name for topic_id, name in topic_rows}
    item_ids = [item.id for item in items]
    people_by_item: dict[UUID, list[str]] = {item_id: [] for item_id in item_ids}
    people_rows = await session.execute(
        select(WorkItemPerson.work_item_id, Person.display_name)
        .join(Person, Person.id == WorkItemPerson.person_id)
        .where(
            WorkItemPerson.user_id == user_id,
            WorkItemPerson.work_item_id.in_(item_ids),
            Person.user_id == user_id,
        )
        .order_by(WorkItemPerson.work_item_id, Person.display_name, Person.id)
    )
    for item_id, display_name in people_rows:
        people_by_item[item_id].append(display_name)
    return [
        WorkItemListEntry(
            item=item,
            topic_name=(
                topic_names.get(item.topic_id) if item.topic_id is not None else None
            ),
            person_names=tuple(people_by_item[item.id]),
        )
        for item in items
    ]
