from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Text, case, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from flowmate.db.models import Person, Topic, WorkItem, WorkItemPerson
from flowmate.task_engine.enums import WorkItemStatus, WorkItemType

OPEN_SEARCH_STATUSES = tuple(
    status.value
    for status in (
        WorkItemStatus.INBOX,
        WorkItemStatus.PLANNED,
        WorkItemStatus.ACTIVE,
        WorkItemStatus.WAITING,
        WorkItemStatus.SNOOZED,
    )
)
ALL_SEARCH_STATUSES = frozenset(status.value for status in WorkItemStatus)
ALL_SEARCH_TYPES = frozenset(item_type.value for item_type in WorkItemType)


@dataclass(frozen=True, slots=True)
class WorkItemSearchFilters:
    text_query: str | None = None
    person_query: str | None = None
    topic_query: str | None = None
    item_types: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    include_all_statuses: bool = False
    due_from: datetime | None = None
    due_to: datetime | None = None
    overdue: bool = False
    stale_contacts: bool = False

    def __post_init__(self) -> None:
        if not set(self.item_types) <= ALL_SEARCH_TYPES:
            raise ValueError("invalid work item search type")
        if not set(self.statuses) <= ALL_SEARCH_STATUSES:
            raise ValueError("invalid work item search status")
        if self.include_all_statuses and self.statuses:
            raise ValueError("all statuses cannot be combined with explicit statuses")
        for name, value in (("due_from", self.due_from), ("due_to", self.due_to)):
            if value is not None and value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.due_from is not None and self.due_to is not None:
            if self.due_from >= self.due_to:
                raise ValueError("search date range must be increasing")
        if self.overdue and (self.due_from is not None or self.due_to is not None):
            raise ValueError("overdue cannot be combined with a date range")

    def to_context(self) -> dict[str, object]:
        return {
            "text_query": self.text_query,
            "person_query": self.person_query,
            "topic_query": self.topic_query,
            "item_types": list(self.item_types),
            "statuses": list(self.statuses),
            "include_all_statuses": self.include_all_statuses,
            "due_from": self.due_from.isoformat() if self.due_from else None,
            "due_to": self.due_to.isoformat() if self.due_to else None,
            "overdue": self.overdue,
            "stale_contacts": self.stale_contacts,
        }

    @classmethod
    def from_context(cls, value: object) -> "WorkItemSearchFilters":
        if not isinstance(value, dict):
            raise ValueError("search filters are missing")

        def optional_text(name: str) -> str | None:
            field = value.get(name)
            if field is None:
                return None
            if not isinstance(field, str) or not field.strip():
                raise ValueError(f"{name} must be non-empty text")
            return field

        def string_tuple(name: str) -> tuple[str, ...]:
            field = value.get(name, [])
            if not isinstance(field, list) or not all(
                isinstance(item, str) for item in field
            ):
                raise ValueError(f"{name} must be a string list")
            return tuple(field)

        def boolean(name: str) -> bool:
            field = value.get(name, False)
            if not isinstance(field, bool):
                raise ValueError(f"{name} must be a boolean")
            return field

        def optional_datetime(name: str) -> datetime | None:
            field = value.get(name)
            if field is None:
                return None
            if not isinstance(field, str):
                raise ValueError(f"{name} must be an ISO datetime")
            try:
                return datetime.fromisoformat(field)
            except ValueError as error:
                raise ValueError(f"{name} must be an ISO datetime") from error

        return cls(
            text_query=optional_text("text_query"),
            person_query=optional_text("person_query"),
            topic_query=optional_text("topic_query"),
            item_types=string_tuple("item_types"),
            statuses=string_tuple("statuses"),
            include_all_statuses=boolean("include_all_statuses"),
            due_from=optional_datetime("due_from"),
            due_to=optional_datetime("due_to"),
            overdue=boolean("overdue"),
            stale_contacts=boolean("stale_contacts"),
        )


@dataclass(frozen=True, slots=True)
class StaleContact:
    person: Person
    work_item: WorkItem
    contact_at: datetime


def effective_search_date() -> ColumnElement[datetime]:
    return case(
        (
            WorkItem.type == WorkItemType.FOLLOW_UP.value,
            func.coalesce(WorkItem.next_follow_up_at, WorkItem.due_at),
        ),
        else_=WorkItem.due_at,
    )


def _escaped_pattern(value: str) -> str:
    escaped = (
        value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def _alias_matches(column: Any, pattern: str) -> ColumnElement[bool]:
    return cast(column, Text).ilike(pattern, escape="\\")


def _person_match(user_id: UUID, pattern: str) -> ColumnElement[bool]:
    return exists(
        select(1)
        .select_from(WorkItemPerson)
        .join(Person, Person.id == WorkItemPerson.person_id)
        .where(
            WorkItemPerson.work_item_id == WorkItem.id,
            WorkItemPerson.user_id == user_id,
            Person.user_id == user_id,
            or_(
                Person.display_name.ilike(pattern, escape="\\"),
                _alias_matches(Person.aliases, pattern),
            ),
        )
    )


def _topic_match(user_id: UUID, pattern: str) -> ColumnElement[bool]:
    return exists(
        select(1).where(
            Topic.id == WorkItem.topic_id,
            Topic.user_id == user_id,
            or_(
                Topic.name.ilike(pattern, escape="\\"),
                _alias_matches(Topic.aliases, pattern),
            ),
        )
    )


async def search_work_items(
    session: AsyncSession,
    user_id: UUID,
    filters: WorkItemSearchFilters,
    *,
    now: datetime,
    limit: int = 11,
    offset: int = 0,
) -> list[WorkItem]:
    if limit <= 0 or offset < 0:
        raise ValueError("invalid search pagination")
    if now.utcoffset() is None:
        raise ValueError("search clock must be timezone-aware")
    effective_date = effective_search_date()
    statement = select(WorkItem).where(WorkItem.user_id == user_id)
    if filters.statuses:
        statement = statement.where(WorkItem.status.in_(filters.statuses))
    elif not filters.include_all_statuses:
        statement = statement.where(WorkItem.status.in_(OPEN_SEARCH_STATUSES))
    if filters.item_types:
        statement = statement.where(WorkItem.type.in_(filters.item_types))
    if filters.text_query:
        pattern = _escaped_pattern(filters.text_query)
        normalized_enum = filters.text_query.strip().casefold().replace("-", "_")
        statement = statement.where(
            or_(
                WorkItem.title.ilike(pattern, escape="\\"),
                WorkItem.description.ilike(pattern, escape="\\"),
                WorkItem.type == normalized_enum,
                WorkItem.status == normalized_enum,
                _person_match(user_id, pattern),
                _topic_match(user_id, pattern),
            )
        )
    if filters.person_query:
        statement = statement.where(
            _person_match(user_id, _escaped_pattern(filters.person_query))
        )
    if filters.topic_query:
        statement = statement.where(
            _topic_match(user_id, _escaped_pattern(filters.topic_query))
        )
    if filters.overdue:
        statement = statement.where(
            WorkItem.status.in_(OPEN_SEARCH_STATUSES),
            effective_date.is_not(None),
            effective_date < now,
        )
    else:
        if filters.due_from is not None:
            statement = statement.where(effective_date >= filters.due_from)
        if filters.due_to is not None:
            statement = statement.where(effective_date < filters.due_to)
    statement = (
        statement.order_by(WorkItem.updated_at.desc(), WorkItem.id)
        .offset(offset)
        .limit(limit)
    )
    return list(await session.scalars(statement))


async def search_stale_contacts(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 11,
    offset: int = 0,
) -> list[StaleContact]:
    if limit <= 0 or offset < 0:
        raise ValueError("invalid search pagination")
    if offset >= 10:
        return []
    limit = min(limit, 10 - offset)
    contact_at = func.coalesce(
        WorkItem.next_follow_up_at,
        WorkItem.due_at,
        WorkItem.updated_at,
    ).label("contact_at")
    rank = (
        func.row_number()
        .over(
            partition_by=Person.id,
            order_by=(contact_at, WorkItem.id),
        )
        .label("contact_rank")
    )
    ranked = (
        select(
            Person.id.label("person_id"),
            WorkItem.id.label("work_item_id"),
            contact_at,
            rank,
        )
        .select_from(Person)
        .join(WorkItemPerson, WorkItemPerson.person_id == Person.id)
        .join(WorkItem, WorkItem.id == WorkItemPerson.work_item_id)
        .where(
            Person.user_id == user_id,
            Person.is_active.is_(True),
            WorkItemPerson.user_id == user_id,
            WorkItem.user_id == user_id,
            WorkItem.status.in_(OPEN_SEARCH_STATUSES),
            WorkItem.type.in_(
                (
                    WorkItemType.FOLLOW_UP.value,
                    WorkItemType.WAITING.value,
                    WorkItemType.QUESTION.value,
                )
            ),
        )
        .subquery()
    )
    rows = await session.execute(
        select(Person, WorkItem, ranked.c.contact_at)
        .join(ranked, ranked.c.person_id == Person.id)
        .join(WorkItem, WorkItem.id == ranked.c.work_item_id)
        .where(
            ranked.c.contact_rank == 1,
            Person.user_id == user_id,
            WorkItem.user_id == user_id,
        )
        .order_by(ranked.c.contact_at, Person.display_name, Person.id)
        .offset(offset)
        .limit(limit)
    )
    return [StaleContact(person, item, contact_at) for person, item, contact_at in rows]
