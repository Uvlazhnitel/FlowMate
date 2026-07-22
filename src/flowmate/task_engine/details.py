from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

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
from flowmate.reminders.sync import ACTIVE_REMINDER_STATUSES


@dataclass(frozen=True, slots=True)
class WorkItemDetails:
    item: WorkItem
    topic_name: str | None
    person_names: tuple[str, ...]
    notes: tuple[Note, ...]
    events: tuple[WorkItemEvent, ...]
    nearest_reminder: Reminder | None


async def get_work_item_details(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    *,
    note_limit: int = 3,
    event_limit: int = 5,
) -> WorkItemDetails | None:
    if note_limit <= 0 or event_limit <= 0:
        raise ValueError("detail limits must be positive")
    item = await session.scalar(
        select(WorkItem).where(
            WorkItem.id == work_item_id,
            WorkItem.user_id == user_id,
        )
    )
    if item is None:
        return None
    topic_name = None
    if item.topic_id is not None:
        topic_name = await session.scalar(
            select(Topic.name).where(
                Topic.id == item.topic_id,
                Topic.user_id == user_id,
            )
        )
    people = tuple(
        await session.scalars(
            select(Person.display_name)
            .join(WorkItemPerson, WorkItemPerson.person_id == Person.id)
            .where(
                WorkItemPerson.work_item_id == item.id,
                WorkItemPerson.user_id == user_id,
                Person.user_id == user_id,
            )
            .order_by(Person.display_name, Person.id)
        )
    )
    linked_ids = select(NoteLink.note_id).where(
        NoteLink.user_id == user_id,
        NoteLink.work_item_id == item.id,
    )
    note_filters: list[ColumnElement[bool]] = [Note.id.in_(linked_ids)]
    if item.source_note_id is not None:
        note_filters.append(Note.id == item.source_note_id)
    notes = tuple(
        await session.scalars(
            select(Note)
            .where(Note.user_id == user_id, or_(*note_filters))
            .order_by(Note.created_at.desc(), Note.id)
            .limit(note_limit)
        )
    )
    events = tuple(
        await session.scalars(
            select(WorkItemEvent)
            .where(
                WorkItemEvent.user_id == user_id,
                WorkItemEvent.work_item_id == item.id,
            )
            .order_by(WorkItemEvent.created_at.desc(), WorkItemEvent.id.desc())
            .limit(event_limit)
        )
    )
    effective_schedule = func.coalesce(
        Reminder.snoozed_until,
        Reminder.next_attempt_at,
        Reminder.scheduled_at,
    )
    reminder = await session.scalar(
        select(Reminder)
        .where(
            Reminder.user_id == user_id,
            Reminder.work_item_id == item.id,
            Reminder.status.in_(ACTIVE_REMINDER_STATUSES),
        )
        .order_by(effective_schedule, Reminder.id)
        .limit(1)
    )
    return WorkItemDetails(item, topic_name, people, notes, events, reminder)
