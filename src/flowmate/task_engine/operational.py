from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import (
    Note,
    NoteLink,
    Person,
    Reminder,
    Topic,
    WorkItem,
    WorkItemEvent,
    WorkItemPerson,
)
from flowmate.reminders.preferences import EffectiveNotificationPreferences
from flowmate.reminders.sync import ACTIVE_REMINDER_STATUSES
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.enums import WorkItemPriority, WorkItemStatus, WorkItemType
from flowmate.task_engine.management import work_item_revision
from flowmate.task_engine.queries import OPEN_STATUSES, validate_pagination

TodaySection = Literal["overdue", "due_today", "follow_ups", "waiting", "questions"]
TopicSection = Literal["active", "people", "notes", "decisions", "history"]
PersonSection = Literal[
    "follow_ups", "waiting", "questions", "topics", "notes", "history"
]


@dataclass(frozen=True, slots=True)
class PageResult:
    items: list[Any]
    limit: int
    offset: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class ReminderCard:
    id: UUID
    effective_at: datetime
    revision: int


@dataclass(frozen=True, slots=True)
class WorkItemCard:
    id: UUID
    type: str
    status: str
    title: str
    description: str | None
    priority: str
    topic_id: UUID | None
    topic_name: str | None
    people: tuple[tuple[UUID, str], ...]
    due_at: datetime | None
    next_follow_up_at: datetime | None
    waiting_since: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    effective_at: datetime | None
    overdue: bool
    revision: int
    reminder: ReminderCard | None


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    id: UUID
    work_item_id: UUID
    title: str
    event_type: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NoteEntry:
    id: UUID
    content: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NamedEntry:
    id: UUID
    name: str
    subtitle: str | None = None


def effective_date(item: WorkItem) -> datetime | None:
    if item.type == WorkItemType.FOLLOW_UP.value:
        return item.next_follow_up_at or item.due_at
    return item.due_at


def local_day_bounds(
    now: datetime, preferences: EffectiveNotificationPreferences
) -> tuple[datetime, datetime]:
    local_now = now.astimezone(preferences.zoneinfo)
    midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0).time()
    start = resolve_local_datetime(
        local_now.date(), midnight, preferences.zoneinfo
    ).astimezone(UTC)
    end = resolve_local_datetime(
        local_now.date() + timedelta(days=1), midnight, preferences.zoneinfo
    ).astimezone(UTC)
    return start, end


def effective_date_sql() -> Any:
    return case(
        (
            WorkItem.type == WorkItemType.FOLLOW_UP.value,
            func.coalesce(WorkItem.next_follow_up_at, WorkItem.due_at),
        ),
        else_=WorkItem.due_at,
    )


async def _page(
    statement: Any, session: AsyncSession, limit: int, offset: int
) -> PageResult:
    validate_pagination(limit, offset)
    values = list(await session.scalars(statement.offset(offset).limit(limit + 1)))
    return PageResult(values[:limit], limit, offset, len(values) > limit)


async def build_work_item_cards(
    session: AsyncSession,
    user_id: UUID,
    items: list[WorkItem],
    *,
    now: datetime,
) -> list[WorkItemCard]:
    if not items:
        return []
    item_ids = [item.id for item in items]
    topic_ids = {item.topic_id for item in items if item.topic_id is not None}
    topic_names: dict[UUID, str] = {}
    if topic_ids:
        rows = await session.execute(
            select(Topic.id, Topic.name).where(
                Topic.user_id == user_id, Topic.id.in_(topic_ids)
            )
        )
        topic_names = {topic_id: name for topic_id, name in rows}
    people: dict[UUID, list[tuple[UUID, str]]] = {item_id: [] for item_id in item_ids}
    rows = await session.execute(
        select(WorkItemPerson.work_item_id, Person.id, Person.display_name)
        .join(Person, Person.id == WorkItemPerson.person_id)
        .where(
            WorkItemPerson.user_id == user_id,
            WorkItemPerson.work_item_id.in_(item_ids),
            Person.user_id == user_id,
        )
        .order_by(WorkItemPerson.work_item_id, Person.display_name, Person.id)
    )
    for item_id, person_id, name in rows:
        people[item_id].append((person_id, name))
    reminders: dict[UUID, Reminder] = {}
    reminder_rows = list(
        await session.scalars(
            select(Reminder)
            .where(
                Reminder.user_id == user_id,
                Reminder.work_item_id.in_(item_ids),
                Reminder.status.in_(ACTIVE_REMINDER_STATUSES),
            )
            .order_by(
                func.coalesce(
                    Reminder.snoozed_until,
                    Reminder.next_attempt_at,
                    Reminder.scheduled_at,
                ),
                Reminder.id,
            )
        )
    )
    for reminder in reminder_rows:
        if reminder.work_item_id is not None:
            reminders.setdefault(reminder.work_item_id, reminder)
    cards: list[WorkItemCard] = []
    for item in items:
        date = effective_date(item)
        selected_reminder = reminders.get(item.id)
        reminder_card = None
        if selected_reminder is not None:
            scheduled = (
                selected_reminder.snoozed_until
                or selected_reminder.next_attempt_at
                or selected_reminder.scheduled_at
            )
            normalized = scheduled.astimezone(UTC)
            epoch = datetime(1970, 1, 1, tzinfo=UTC)
            delta = normalized - epoch
            reminder_card = ReminderCard(
                selected_reminder.id,
                scheduled,
                delta.days * 86_400_000_000
                + delta.seconds * 1_000_000
                + delta.microseconds,
            )
        cards.append(
            WorkItemCard(
                id=item.id,
                type=item.type,
                status=item.status,
                title=item.title,
                description=item.description,
                priority=item.priority,
                topic_id=item.topic_id,
                topic_name=topic_names.get(item.topic_id) if item.topic_id else None,
                people=tuple(people[item.id]),
                due_at=item.due_at,
                next_follow_up_at=item.next_follow_up_at,
                waiting_since=item.waiting_since,
                completed_at=item.completed_at,
                updated_at=item.updated_at,
                effective_at=date,
                overdue=date is not None and date < now,
                revision=work_item_revision(item.updated_at),
                reminder=reminder_card,
            )
        )
    return cards


async def list_today_section(
    session: AsyncSession,
    user_id: UUID,
    section: TodaySection,
    *,
    now: datetime,
    preferences: EffectiveNotificationPreferences,
    limit: int,
    offset: int,
) -> PageResult:
    start, end = local_day_bounds(now, preferences)
    statement = select(WorkItem).where(
        WorkItem.user_id == user_id, WorkItem.status.in_(OPEN_STATUSES)
    )
    semantic_types = (
        WorkItemType.FOLLOW_UP.value,
        WorkItemType.WAITING.value,
        WorkItemType.QUESTION.value,
    )
    if section == "follow_ups":
        statement = statement.where(
            WorkItem.type == WorkItemType.FOLLOW_UP.value,
            WorkItem.next_follow_up_at < end,
        ).order_by(WorkItem.next_follow_up_at, WorkItem.id)
    elif section == "waiting":
        statement = statement.where(
            WorkItem.type == WorkItemType.WAITING.value, WorkItem.due_at < end
        ).order_by(WorkItem.due_at, WorkItem.id)
    elif section == "questions":
        statement = statement.where(
            WorkItem.type == WorkItemType.QUESTION.value
        ).order_by(WorkItem.due_at.asc().nulls_last(), WorkItem.created_at, WorkItem.id)
    elif section == "overdue":
        statement = statement.where(
            WorkItem.type.not_in(semantic_types), WorkItem.due_at < start
        ).order_by(WorkItem.due_at, WorkItem.id)
    else:
        statement = statement.where(
            WorkItem.type.not_in(semantic_types),
            WorkItem.due_at >= start,
            WorkItem.due_at < end,
        ).order_by(WorkItem.due_at, WorkItem.id)
    page = await _page(statement, session, limit, offset)
    return PageResult(
        list(await build_work_item_cards(session, user_id, page.items, now=now)),
        limit,
        offset,
        page.has_more,
    )


async def dashboard_snapshot(
    session: AsyncSession,
    user_id: UUID,
    *,
    now: datetime,
    preferences: EffectiveNotificationPreferences,
) -> dict[str, object]:
    start, end = local_day_bounds(now, preferences)
    owned_open = (WorkItem.user_id == user_id, WorkItem.status.in_(OPEN_STATUSES))
    semantic_types = (
        WorkItemType.FOLLOW_UP.value,
        WorkItemType.WAITING.value,
        WorkItemType.QUESTION.value,
    )

    async def count(*conditions: Any) -> int:
        return int(
            (await session.scalar(select(func.count(WorkItem.id)).where(*conditions)))
            or 0
        )

    summary = {
        "overdue": await count(
            *owned_open, WorkItem.type.not_in(semantic_types), WorkItem.due_at < start
        ),
        "due_today": await count(
            *owned_open,
            WorkItem.type.not_in(semantic_types),
            WorkItem.due_at >= start,
            WorkItem.due_at < end,
        ),
        "follow_ups": await count(
            *owned_open,
            WorkItem.type == WorkItemType.FOLLOW_UP.value,
            WorkItem.next_follow_up_at < end,
        ),
        "waiting_overdue": await count(
            *owned_open,
            WorkItem.type == WorkItemType.WAITING.value,
            WorkItem.due_at < start,
        ),
        "questions": await count(
            *owned_open, WorkItem.type == WorkItemType.QUESTION.value
        ),
        "inbox": await count(
            WorkItem.user_id == user_id,
            WorkItem.status == WorkItemStatus.INBOX.value,
        ),
        "planner_queue": int(
            (
                await session.scalar(
                    select(func.count(WorkItem.id)).where(
                        WorkItem.user_id == user_id,
                        WorkItem.planner_status.in_(
                            ("needs_transfer", "update_required")
                        ),
                    )
                )
            )
            or 0
        ),
    }
    candidates = list(
        await session.scalars(
            select(WorkItem)
            .where(*owned_open)
            .order_by(WorkItem.updated_at.desc(), WorkItem.id)
            .limit(100)
        )
    )
    priority_rank = {
        WorkItemPriority.URGENT.value: 0,
        WorkItemPriority.HIGH.value: 1,
        WorkItemPriority.NORMAL.value: 2,
        WorkItemPriority.LOW.value: 3,
    }

    def recommendation_key(item: WorkItem) -> tuple[int, int, datetime, UUID]:
        date = effective_date(item)
        if item.type == WorkItemType.FOLLOW_UP.value and date and date < end:
            category = 1
        elif item.type == WorkItemType.WAITING.value and date and date < start:
            category = 2
        elif date and date < start:
            category = 0
        elif date and date < end:
            category = 3
        elif item.type == WorkItemType.QUESTION.value:
            category = 4
        else:
            category = 5
        return (
            category,
            priority_rank.get(item.priority, 2),
            date or datetime.max.replace(tzinfo=UTC),
            item.id,
        )

    recommended_items = [
        item
        for item in sorted(candidates, key=recommendation_key)
        if recommendation_key(item)[0] < 5
    ][:5]
    upcoming_items = sorted(
        [
            item
            for item in candidates
            if (item_date := effective_date(item)) is not None and item_date >= now
        ],
        key=lambda item: (effective_date(item), item.id),
    )[:10]
    activity_rows = await session.execute(
        select(WorkItemEvent, WorkItem.title)
        .join(WorkItem, WorkItem.id == WorkItemEvent.work_item_id)
        .where(WorkItemEvent.user_id == user_id, WorkItem.user_id == user_id)
        .order_by(WorkItemEvent.created_at.desc(), WorkItemEvent.id.desc())
        .limit(10)
    )
    activity = [
        ActivityEntry(
            event.id, event.work_item_id, title, event.event_type, event.created_at
        )
        for event, title in activity_rows
    ]
    return {
        "summary": summary,
        "recommended": await build_work_item_cards(
            session, user_id, recommended_items, now=now
        ),
        "activity": activity,
        "deadlines": await build_work_item_cards(
            session, user_id, upcoming_items, now=now
        ),
    }


async def list_topics_summary(
    session: AsyncSession,
    user_id: UUID,
    *,
    query: str | None,
    now: datetime,
    limit: int,
    offset: int,
) -> PageResult:
    validate_pagination(limit, offset)
    effective = effective_date_sql()
    base = WorkItem.status.in_(OPEN_STATUSES)
    statement = (
        select(
            Topic,
            func.count(WorkItem.id).filter(base).label("open_count"),
            func.count(WorkItem.id)
            .filter(base, effective < now)
            .label("overdue_count"),
            func.count(WorkItem.id)
            .filter(base, WorkItem.type == WorkItemType.FOLLOW_UP.value)
            .label("follow_up_count"),
            func.count(WorkItem.id)
            .filter(base, WorkItem.type == WorkItemType.WAITING.value)
            .label("waiting_count"),
            func.min(effective).filter(base, effective >= now).label("next_deadline"),
        )
        .outerjoin(
            WorkItem, (WorkItem.topic_id == Topic.id) & (WorkItem.user_id == user_id)
        )
        .where(Topic.user_id == user_id, Topic.is_active.is_(True))
        .group_by(Topic.id)
    )
    if query:
        statement = statement.where(Topic.name.ilike(f"%{query.strip()}%"))
    rows = list(
        (
            await session.execute(
                statement.order_by(
                    func.count(WorkItem.id).filter(base).desc(), Topic.name, Topic.id
                )
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    )
    values: list[Any] = []
    for row in rows[:limit]:
        (
            topic,
            open_count,
            overdue_count,
            follow_up_count,
            waiting_count,
            next_deadline,
        ) = row
        values.append(
            {
                "id": topic.id,
                "name": topic.name,
                "description": topic.description,
                "open_count": open_count,
                "overdue_count": overdue_count,
                "follow_up_count": follow_up_count,
                "waiting_count": waiting_count,
                "next_deadline": next_deadline,
            }
        )
    return PageResult(values, limit, offset, len(rows) > limit)


async def get_owned_topic(
    session: AsyncSession, user_id: UUID, topic_id: UUID
) -> Topic | None:
    return cast(
        Topic | None,
        await session.scalar(
            select(Topic).where(
                Topic.id == topic_id,
                Topic.user_id == user_id,
                Topic.is_active.is_(True),
            )
        ),
    )


async def list_people_summary(
    session: AsyncSession,
    user_id: UUID,
    *,
    query: str | None,
    limit: int,
    offset: int,
) -> PageResult:
    validate_pagination(limit, offset)
    base = WorkItem.status.in_(OPEN_STATUSES)
    last_activity = func.max(WorkItem.updated_at)
    statement = (
        select(
            Person,
            func.count(WorkItem.id).filter(
                base, WorkItem.type == WorkItemType.FOLLOW_UP.value
            ),
            func.count(WorkItem.id).filter(
                base, WorkItem.type == WorkItemType.WAITING.value
            ),
            func.count(WorkItem.id).filter(
                base, WorkItem.type == WorkItemType.QUESTION.value
            ),
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
    if query:
        statement = statement.where(Person.display_name.ilike(f"%{query.strip()}%"))
    rows = list(
        (
            await session.execute(
                statement.order_by(
                    last_activity.desc().nulls_last(), Person.display_name, Person.id
                )
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
    )
    values = [
        {
            "id": person.id,
            "display_name": person.display_name,
            "role": person.role,
            "follow_up_count": follow_ups,
            "waiting_count": waiting,
            "question_count": questions,
            "last_activity": activity or person.updated_at,
        }
        for person, follow_ups, waiting, questions, activity in rows[:limit]
    ]
    return PageResult(values, limit, offset, len(rows) > limit)


async def get_owned_person(
    session: AsyncSession, user_id: UUID, person_id: UUID
) -> Person | None:
    return cast(
        Person | None,
        await session.scalar(
            select(Person).where(
                Person.id == person_id,
                Person.user_id == user_id,
                Person.is_active.is_(True),
            )
        ),
    )


async def list_context_content(
    session: AsyncSession,
    user_id: UUID,
    *,
    owner_type: Literal["topic", "person"],
    owner_id: UUID,
    section: str,
    now: datetime,
    limit: int,
    offset: int,
) -> PageResult:
    validate_pagination(limit, offset)
    if owner_type == "topic":
        item_filter = WorkItem.topic_id == owner_id
        item_ids = select(WorkItem.id).where(WorkItem.user_id == user_id, item_filter)
    else:
        item_ids = select(WorkItemPerson.work_item_id).where(
            WorkItemPerson.user_id == user_id, WorkItemPerson.person_id == owner_id
        )
        item_filter = WorkItem.id.in_(item_ids)
    if section in {"active", "decisions", "follow_ups", "waiting", "questions"}:
        statement = select(WorkItem).where(WorkItem.user_id == user_id, item_filter)
        if section == "active":
            statement = statement.where(
                WorkItem.status.in_(OPEN_STATUSES),
                WorkItem.type != WorkItemType.DECISION.value,
            )
        elif section == "decisions":
            statement = statement.where(
                WorkItem.type == WorkItemType.DECISION.value,
                WorkItem.status.not_in(
                    (WorkItemStatus.CANCELLED.value, WorkItemStatus.ARCHIVED.value)
                ),
            )
        else:
            kind = {
                "follow_ups": WorkItemType.FOLLOW_UP.value,
                "waiting": WorkItemType.WAITING.value,
                "questions": WorkItemType.QUESTION.value,
            }[section]
            statement = statement.where(
                WorkItem.status.in_(OPEN_STATUSES), WorkItem.type == kind
            )
        page = await _page(
            statement.order_by(
                effective_date_sql().asc().nulls_last(),
                WorkItem.updated_at.desc(),
                WorkItem.id,
            ),
            session,
            limit,
            offset,
        )
        return PageResult(
            list(await build_work_item_cards(session, user_id, page.items, now=now)),
            limit,
            offset,
            page.has_more,
        )
    if section == "people":
        people_statement = (
            select(Person)
            .join(WorkItemPerson, WorkItemPerson.person_id == Person.id)
            .join(WorkItem, WorkItem.id == WorkItemPerson.work_item_id)
            .where(
                Person.user_id == user_id,
                WorkItemPerson.user_id == user_id,
                WorkItem.user_id == user_id,
                WorkItem.topic_id == owner_id,
            )
            .distinct()
            .order_by(Person.display_name, Person.id)
        )
        page = await _page(people_statement, session, limit, offset)
        return PageResult(
            [
                NamedEntry(person.id, person.display_name, person.role)
                for person in page.items
            ],
            limit,
            offset,
            page.has_more,
        )
    if section == "topics":
        topics_statement = (
            select(Topic)
            .join(WorkItem, WorkItem.topic_id == Topic.id)
            .where(
                Topic.user_id == user_id,
                WorkItem.user_id == user_id,
                WorkItem.id.in_(item_ids),
            )
            .distinct()
            .order_by(Topic.name, Topic.id)
        )
        page = await _page(topics_statement, session, limit, offset)
        return PageResult(
            [
                NamedEntry(topic.id, topic.name, topic.description)
                for topic in page.items
            ],
            limit,
            offset,
            page.has_more,
        )
    if section == "notes":
        direct_filter = (
            NoteLink.topic_id == owner_id
            if owner_type == "topic"
            else NoteLink.person_id == owner_id
        )
        direct_notes = select(NoteLink.note_id).where(
            NoteLink.user_id == user_id, direct_filter
        )
        work_notes = select(NoteLink.note_id).where(
            NoteLink.user_id == user_id, NoteLink.work_item_id.in_(item_ids)
        )
        notes_statement = (
            select(Note)
            .where(
                Note.user_id == user_id,
                or_(Note.id.in_(direct_notes), Note.id.in_(work_notes)),
            )
            .order_by(Note.created_at.desc(), Note.id)
        )
        page = await _page(notes_statement, session, limit, offset)
        return PageResult(
            [NoteEntry(note.id, note.content, note.created_at) for note in page.items],
            limit,
            offset,
            page.has_more,
        )
    history_statement = (
        select(WorkItemEvent, WorkItem.title)
        .join(WorkItem, WorkItem.id == WorkItemEvent.work_item_id)
        .where(
            WorkItemEvent.user_id == user_id, WorkItem.user_id == user_id, item_filter
        )
        .order_by(WorkItemEvent.created_at.desc(), WorkItemEvent.id.desc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list((await session.execute(history_statement)).all())
    return PageResult(
        [
            ActivityEntry(
                event.id, event.work_item_id, title, event.event_type, event.created_at
            )
            for event, title in rows[:limit]
        ],
        limit,
        offset,
        len(rows) > limit,
    )


async def list_agenda(
    session: AsyncSession,
    user_id: UUID,
    *,
    group_kind: str | None,
    group_id: UUID | None,
    now: datetime,
    limit: int,
    offset: int,
) -> PageResult:
    validate_pagination(limit, offset)
    items = list(
        await session.scalars(
            select(WorkItem)
            .where(
                WorkItem.user_id == user_id,
                WorkItem.status.in_(OPEN_STATUSES),
                WorkItem.type.in_(
                    (WorkItemType.AGENDA_ITEM.value, WorkItemType.QUESTION.value)
                ),
            )
            .order_by(
                WorkItem.due_at.asc().nulls_last(), WorkItem.created_at, WorkItem.id
            )
        )
    )
    cards = await build_work_item_cards(session, user_id, items, now=now)
    grouped: list[dict[str, Any]] = []
    for card in cards:
        if card.people:
            kind, identifier, label = "person", card.people[0][0], card.people[0][1]
        elif card.topic_id is not None:
            kind, identifier, label = "topic", card.topic_id, card.topic_name or "Тема"
        else:
            kind, identifier, label = "unassigned", None, "Без привязки"
        if group_kind and kind != group_kind:
            continue
        if group_id and identifier != group_id:
            continue
        grouped.append(
            {
                "group_kind": kind,
                "group_id": identifier,
                "group_label": label,
                "item": card,
            }
        )
    grouped.sort(
        key=lambda row: (
            str(row["group_kind"]),
            str(row["group_label"]),
            str(row["item"].id),
        )
    )
    return PageResult(
        grouped[offset : offset + limit], limit, offset, len(grouped) > offset + limit
    )
