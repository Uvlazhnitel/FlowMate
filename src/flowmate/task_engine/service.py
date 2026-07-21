from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import (
    DraftItemRecord,
    DraftSession,
    Note,
    NoteLink,
    Person,
    Topic,
    WorkItem,
    WorkItemEvent,
    WorkItemPerson,
    WorkItemRelation,
)
from flowmate.task_engine.enums import (
    NoteTargetType,
    WorkItemEventType,
    WorkItemPriority,
    WorkItemRelationType,
    WorkItemStatus,
    WorkItemType,
)


def normalize_required_text(value: str, field_name: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def normalize_aliases(values: Iterable[str], primary_name: str) -> list[str]:
    primary = primary_name.casefold()
    aliases: list[str] = []
    seen = {primary}
    for value in values:
        normalized = " ".join(value.split()).casefold()
        if normalized and normalized not in seen:
            aliases.append(normalized)
            seen.add(normalized)
    return aliases


def validate_aware_datetime(value: datetime | None, field_name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{field_name} must be timezone-aware")


def parse_work_item_type(value: WorkItemType | str) -> WorkItemType:
    return WorkItemType(value)


def parse_work_item_status(value: WorkItemStatus | str) -> WorkItemStatus:
    return WorkItemStatus(value)


def parse_work_item_priority(value: WorkItemPriority | str) -> WorkItemPriority:
    return WorkItemPriority(value)


def parse_relation_type(
    value: WorkItemRelationType | str,
) -> WorkItemRelationType:
    return WorkItemRelationType(value)


def parse_event_type(value: WorkItemEventType | str) -> WorkItemEventType:
    return WorkItemEventType(value)


def parse_note_target_type(value: NoteTargetType | str) -> NoteTargetType:
    return NoteTargetType(value)


async def get_topic(
    session: AsyncSession,
    user_id: UUID,
    topic_id: UUID,
) -> Topic | None:
    return (
        await session.scalars(
            select(Topic).where(Topic.id == topic_id, Topic.user_id == user_id)
        )
    ).one_or_none()


async def find_topics(
    session: AsyncSession,
    user_id: UUID,
    name_or_alias: str,
) -> list[Topic]:
    normalized = normalize_required_text(name_or_alias, "name_or_alias").casefold()
    statement = (
        select(Topic)
        .where(
            Topic.user_id == user_id,
            or_(
                func.lower(func.btrim(Topic.name)) == normalized,
                Topic.aliases.contains([normalized]),
            ),
        )
        .order_by(Topic.name)
    )
    return list(await session.scalars(statement))


async def list_topics(
    session: AsyncSession,
    user_id: UUID,
    *,
    active_only: bool = True,
) -> list[Topic]:
    statement = select(Topic).where(Topic.user_id == user_id)
    if active_only:
        statement = statement.where(Topic.is_active.is_(True))
    return list(await session.scalars(statement.order_by(Topic.name)))


async def create_topic(
    session: AsyncSession,
    user_id: UUID,
    name: str,
    *,
    description: str | None = None,
    aliases: Iterable[str] = (),
) -> Topic:
    normalized_name = normalize_required_text(name, "name")
    topic = Topic(
        user_id=user_id,
        name=normalized_name,
        description=normalize_optional_text(description),
        aliases=normalize_aliases(aliases, normalized_name),
    )
    session.add(topic)
    await session.flush()
    return topic


async def get_or_create_topic(
    session: AsyncSession,
    user_id: UUID,
    name: str,
    *,
    description: str | None = None,
    aliases: Iterable[str] = (),
) -> tuple[Topic, bool]:
    normalized_name = normalize_required_text(name, "name")
    existing = await find_topics(session, user_id, normalized_name)
    exact = next(
        (
            topic
            for topic in existing
            if topic.name.strip().casefold() == normalized_name.casefold()
        ),
        None,
    )
    if exact is not None:
        return exact, False

    try:
        async with session.begin_nested():
            topic = await create_topic(
                session,
                user_id,
                normalized_name,
                description=description,
                aliases=aliases,
            )
        return topic, True
    except IntegrityError:
        matches = await find_topics(session, user_id, normalized_name)
        exact = next(
            (
                topic
                for topic in matches
                if topic.name.strip().casefold() == normalized_name.casefold()
            ),
            None,
        )
        if exact is None:
            raise
        return exact, False


async def get_person(
    session: AsyncSession,
    user_id: UUID,
    person_id: UUID,
) -> Person | None:
    return (
        await session.scalars(
            select(Person).where(Person.id == person_id, Person.user_id == user_id)
        )
    ).one_or_none()


async def find_people(
    session: AsyncSession,
    user_id: UUID,
    name_or_alias: str,
) -> list[Person]:
    normalized = normalize_required_text(name_or_alias, "name_or_alias").casefold()
    statement = (
        select(Person)
        .where(
            Person.user_id == user_id,
            or_(
                func.lower(func.btrim(Person.display_name)) == normalized,
                Person.aliases.contains([normalized]),
            ),
        )
        .order_by(Person.display_name, Person.created_at)
    )
    return list(await session.scalars(statement))


async def list_people(
    session: AsyncSession,
    user_id: UUID,
    *,
    active_only: bool = True,
) -> list[Person]:
    statement = select(Person).where(Person.user_id == user_id)
    if active_only:
        statement = statement.where(Person.is_active.is_(True))
    return list(
        await session.scalars(
            statement.order_by(Person.display_name, Person.created_at)
        )
    )


async def create_person(
    session: AsyncSession,
    user_id: UUID,
    display_name: str,
    *,
    role: str | None = None,
    notes: str | None = None,
    aliases: Iterable[str] = (),
) -> Person:
    normalized_name = normalize_required_text(display_name, "display_name")
    person = Person(
        user_id=user_id,
        display_name=normalized_name,
        role=normalize_optional_text(role),
        notes=normalize_optional_text(notes),
        aliases=normalize_aliases(aliases, normalized_name),
    )
    session.add(person)
    await session.flush()
    return person


async def get_work_item(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
) -> WorkItem | None:
    return (
        await session.scalars(
            select(WorkItem).where(
                WorkItem.id == work_item_id,
                WorkItem.user_id == user_id,
            )
        )
    ).one_or_none()


async def list_work_items(
    session: AsyncSession,
    user_id: UUID,
    *,
    status: WorkItemStatus | str | None = None,
    item_type: WorkItemType | str | None = None,
) -> list[WorkItem]:
    statement = select(WorkItem).where(WorkItem.user_id == user_id)
    if status is not None:
        statement = statement.where(
            WorkItem.status == parse_work_item_status(status).value
        )
    if item_type is not None:
        statement = statement.where(
            WorkItem.type == parse_work_item_type(item_type).value
        )
    return list(await session.scalars(statement.order_by(WorkItem.created_at.desc())))


async def require_owned_note(
    session: AsyncSession,
    user_id: UUID,
    note_id: UUID,
) -> Note:
    note = await session.scalar(
        select(Note).where(Note.id == note_id, Note.user_id == user_id)
    )
    if note is None:
        raise ValueError("note not found")
    return note


async def require_owned_draft_item(
    session: AsyncSession,
    user_id: UUID,
    draft_item_id: UUID,
) -> DraftItemRecord:
    statement = (
        select(DraftItemRecord)
        .join(DraftSession, DraftSession.id == DraftItemRecord.draft_session_id)
        .where(
            DraftItemRecord.id == draft_item_id,
            DraftSession.user_id == user_id,
        )
    )
    draft_item = await session.scalar(statement)
    if draft_item is None:
        raise ValueError("draft item not found")
    return draft_item


async def create_work_item(
    session: AsyncSession,
    user_id: UUID,
    *,
    item_type: WorkItemType | str,
    title: str,
    description: str | None = None,
    status: WorkItemStatus | str = WorkItemStatus.INBOX,
    priority: WorkItemPriority | str = WorkItemPriority.NORMAL,
    topic_id: UUID | None = None,
    due_at: datetime | None = None,
    next_follow_up_at: datetime | None = None,
    waiting_since: datetime | None = None,
    completed_at: datetime | None = None,
    source_note_id: UUID | None = None,
    source_draft_item_id: UUID | None = None,
) -> WorkItem:
    parsed_type = parse_work_item_type(item_type)
    parsed_status = parse_work_item_status(status)
    parsed_priority = parse_work_item_priority(priority)
    normalized_title = normalize_required_text(title, "title")
    for field_name, value in (
        ("due_at", due_at),
        ("next_follow_up_at", next_follow_up_at),
        ("waiting_since", waiting_since),
        ("completed_at", completed_at),
    ):
        validate_aware_datetime(value, field_name)

    if topic_id is not None and await get_topic(session, user_id, topic_id) is None:
        raise ValueError("topic not found")
    if source_note_id is not None:
        await require_owned_note(session, user_id, source_note_id)
    if source_draft_item_id is not None:
        await require_owned_draft_item(session, user_id, source_draft_item_id)

    work_item = WorkItem(
        user_id=user_id,
        type=parsed_type.value,
        title=normalized_title,
        description=normalize_optional_text(description),
        status=parsed_status.value,
        priority=parsed_priority.value,
        topic_id=topic_id,
        due_at=due_at,
        next_follow_up_at=next_follow_up_at,
        waiting_since=waiting_since,
        completed_at=completed_at,
        source_note_id=source_note_id,
        source_draft_item_id=source_draft_item_id,
    )
    session.add(work_item)
    await session.flush()
    session.add(
        WorkItemEvent(
            user_id=user_id,
            work_item_id=work_item.id,
            event_type=WorkItemEventType.CREATED.value,
            payload={
                "type": parsed_type.value,
                "status": parsed_status.value,
                "priority": parsed_priority.value,
            },
        )
    )
    await session.flush()
    return work_item


async def link_person_to_work_item(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    person_id: UUID,
    *,
    role: str | None = None,
) -> tuple[WorkItemPerson, bool]:
    if await get_work_item(session, user_id, work_item_id) is None:
        raise ValueError("work item not found")
    if await get_person(session, user_id, person_id) is None:
        raise ValueError("person not found")
    existing = await session.scalar(
        select(WorkItemPerson).where(
            WorkItemPerson.user_id == user_id,
            WorkItemPerson.work_item_id == work_item_id,
            WorkItemPerson.person_id == person_id,
        )
    )
    if existing is not None:
        return existing, False
    association = WorkItemPerson(
        user_id=user_id,
        work_item_id=work_item_id,
        person_id=person_id,
        role=normalize_optional_text(role),
    )
    session.add(association)
    await session.flush()
    return association, True


async def list_people_for_work_item(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
) -> list[Person]:
    if await get_work_item(session, user_id, work_item_id) is None:
        return []
    statement = (
        select(Person)
        .join(WorkItemPerson, WorkItemPerson.person_id == Person.id)
        .where(
            WorkItemPerson.user_id == user_id,
            WorkItemPerson.work_item_id == work_item_id,
            Person.user_id == user_id,
        )
        .order_by(Person.display_name, Person.created_at)
    )
    return list(await session.scalars(statement))


async def create_work_item_relation(
    session: AsyncSession,
    user_id: UUID,
    source_work_item_id: UUID,
    target_work_item_id: UUID,
    relation_type: WorkItemRelationType | str,
) -> WorkItemRelation:
    parsed_type = parse_relation_type(relation_type)
    if source_work_item_id == target_work_item_id:
        raise ValueError("work item relation cannot reference itself")
    if await get_work_item(session, user_id, source_work_item_id) is None:
        raise ValueError("source work item not found")
    if await get_work_item(session, user_id, target_work_item_id) is None:
        raise ValueError("target work item not found")
    relation = WorkItemRelation(
        user_id=user_id,
        source_work_item_id=source_work_item_id,
        target_work_item_id=target_work_item_id,
        relation_type=parsed_type.value,
    )
    session.add(relation)
    await session.flush()
    return relation


async def list_work_item_relations(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
) -> list[WorkItemRelation]:
    if await get_work_item(session, user_id, work_item_id) is None:
        return []
    statement = (
        select(WorkItemRelation)
        .where(
            WorkItemRelation.user_id == user_id,
            or_(
                WorkItemRelation.source_work_item_id == work_item_id,
                WorkItemRelation.target_work_item_id == work_item_id,
            ),
        )
        .order_by(WorkItemRelation.created_at)
    )
    return list(await session.scalars(statement))


async def require_note_target(
    session: AsyncSession,
    user_id: UUID,
    target_type: NoteTargetType,
    target_id: UUID,
) -> None:
    if target_type is NoteTargetType.WORK_ITEM:
        target_exists = await get_work_item(session, user_id, target_id) is not None
    elif target_type is NoteTargetType.PERSON:
        target_exists = await get_person(session, user_id, target_id) is not None
    else:
        target_exists = await get_topic(session, user_id, target_id) is not None
    if not target_exists:
        raise ValueError(f"{target_type.value} not found")


def note_link_target(
    target_type: NoteTargetType,
    target_id: UUID,
) -> tuple[Any, dict[str, UUID]]:
    if target_type is NoteTargetType.WORK_ITEM:
        return NoteLink.work_item_id == target_id, {"work_item_id": target_id}
    if target_type is NoteTargetType.PERSON:
        return NoteLink.person_id == target_id, {"person_id": target_id}
    return NoteLink.topic_id == target_id, {"topic_id": target_id}


async def link_note(
    session: AsyncSession,
    user_id: UUID,
    note_id: UUID,
    target_type: NoteTargetType | str,
    target_id: UUID,
) -> tuple[NoteLink, bool]:
    parsed_target = parse_note_target_type(target_type)
    await require_owned_note(session, user_id, note_id)
    await require_note_target(session, user_id, parsed_target, target_id)
    target_filter, target_values = note_link_target(parsed_target, target_id)
    existing = await session.scalar(
        select(NoteLink).where(
            NoteLink.user_id == user_id,
            NoteLink.note_id == note_id,
            target_filter,
        )
    )
    if existing is not None:
        return existing, False
    note_link = NoteLink(user_id=user_id, note_id=note_id, **target_values)
    session.add(note_link)
    await session.flush()
    return note_link, True


async def create_linked_note(
    session: AsyncSession,
    user_id: UUID,
    *,
    content: str,
    target_type: NoteTargetType | str,
    target_id: UUID,
) -> tuple[Note, NoteLink]:
    parsed_target = parse_note_target_type(target_type)
    await require_note_target(session, user_id, parsed_target, target_id)
    note = Note(
        user_id=user_id,
        content=normalize_required_text(content, "content"),
        source="manual",
        telegram_update_id=None,
    )
    session.add(note)
    await session.flush()
    note_link, _ = await link_note(
        session,
        user_id,
        note.id,
        parsed_target,
        target_id,
    )
    return note, note_link


async def list_linked_notes(
    session: AsyncSession,
    user_id: UUID,
    target_type: NoteTargetType | str,
    target_id: UUID,
) -> list[Note]:
    parsed_target = parse_note_target_type(target_type)
    if parsed_target is NoteTargetType.WORK_ITEM:
        target_exists = await get_work_item(session, user_id, target_id) is not None
    elif parsed_target is NoteTargetType.PERSON:
        target_exists = await get_person(session, user_id, target_id) is not None
    else:
        target_exists = await get_topic(session, user_id, target_id) is not None
    if not target_exists:
        return []
    target_filter, _ = note_link_target(parsed_target, target_id)
    statement = (
        select(Note)
        .join(NoteLink, NoteLink.note_id == Note.id)
        .where(
            Note.user_id == user_id,
            NoteLink.user_id == user_id,
            target_filter,
        )
        .order_by(Note.created_at.desc())
    )
    return list(await session.scalars(statement))


async def append_work_item_event(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    event_type: WorkItemEventType | str,
    *,
    payload: dict[str, Any] | None = None,
) -> WorkItemEvent:
    parsed_type = parse_event_type(event_type)
    if await get_work_item(session, user_id, work_item_id) is None:
        raise ValueError("work item not found")
    event = WorkItemEvent(
        user_id=user_id,
        work_item_id=work_item_id,
        event_type=parsed_type.value,
        payload=dict(payload or {}),
    )
    session.add(event)
    await session.flush()
    return event


async def list_work_item_events(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
) -> list[WorkItemEvent]:
    if await get_work_item(session, user_id, work_item_id) is None:
        return []
    statement: Select[tuple[WorkItemEvent]] = (
        select(WorkItemEvent)
        .where(
            WorkItemEvent.user_id == user_id,
            WorkItemEvent.work_item_id == work_item_id,
        )
        .order_by(WorkItemEvent.created_at, WorkItemEvent.id)
    )
    return list(await session.scalars(statement))
