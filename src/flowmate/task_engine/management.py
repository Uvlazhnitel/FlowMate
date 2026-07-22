from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import (
    Note,
    WorkItem,
    WorkItemEvent,
    WorkItemPerson,
    WorkItemRelation,
)
from flowmate.reminders.sync import (
    ReminderPolicy,
    cancel_work_item_reminders,
    sync_work_item_reminders,
)
from flowmate.task_engine.enums import (
    NoteTargetType,
    WorkItemEventType,
    WorkItemRelationType,
    WorkItemStatus,
    WorkItemType,
)
from flowmate.task_engine.queries import OPEN_STATUSES
from flowmate.task_engine.service import (
    create_linked_note,
    create_work_item,
    create_work_item_relation,
    get_person,
    get_topic,
    link_person_to_work_item,
    validate_aware_datetime,
)


class InvalidWorkItemTransitionError(ValueError):
    """The requested work item state transition is not allowed."""


class StaleWorkItemError(InvalidWorkItemTransitionError):
    """The work item changed after a Telegram card was rendered."""


def work_item_revision(value: datetime) -> int:
    normalized = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = normalized - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


@dataclass(frozen=True, slots=True)
class MutationResult:
    work_item: WorkItem
    event: WorkItemEvent
    changed: bool


def management_now() -> datetime:
    return datetime.now(UTC)


async def event_for_update(
    session: AsyncSession,
    user_id: UUID,
    telegram_update_id: int,
) -> WorkItemEvent | None:
    return (
        await session.scalars(
            select(WorkItemEvent).where(
                WorkItemEvent.user_id == user_id,
                WorkItemEvent.telegram_update_id == telegram_update_id,
            )
        )
    ).one_or_none()


async def lock_work_item(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    *,
    expected_revision: int | None = None,
) -> WorkItem:
    item = await session.scalar(
        select(WorkItem)
        .where(WorkItem.id == work_item_id, WorkItem.user_id == user_id)
        .with_for_update()
    )
    if item is None:
        raise ValueError("work item not found")
    if (
        expected_revision is not None
        and work_item_revision(item.updated_at) != expected_revision
    ):
        raise StaleWorkItemError("work item card is stale")
    return item


async def append_management_event(
    session: AsyncSession,
    item: WorkItem,
    event_type: WorkItemEventType,
    telegram_update_id: int,
    payload: dict[str, object],
) -> WorkItemEvent:
    event = WorkItemEvent(
        user_id=item.user_id,
        work_item_id=item.id,
        event_type=event_type.value,
        telegram_update_id=telegram_update_id,
        payload=payload,
    )
    session.add(event)
    await session.flush()
    return event


async def existing_mutation(
    session: AsyncSession,
    user_id: UUID,
    telegram_update_id: int,
) -> MutationResult | None:
    event = await event_for_update(session, user_id, telegram_update_id)
    if event is None:
        return None
    item = await lock_work_item(session, user_id, event.work_item_id)
    return MutationResult(item, event, False)


async def complete_work_item(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    telegram_update_id: int,
    *,
    now: datetime | None = None,
    expected_revision: int | None = None,
) -> MutationResult:
    duplicate = await existing_mutation(session, user_id, telegram_update_id)
    if duplicate is not None:
        return duplicate
    item = await lock_work_item(
        session, user_id, work_item_id, expected_revision=expected_revision
    )
    if item.status not in OPEN_STATUSES:
        raise InvalidWorkItemTransitionError("only open work items can be completed")
    previous = item.status
    completed_at = now or management_now()
    item.status = WorkItemStatus.DONE.value
    item.completed_at = completed_at
    event = await append_management_event(
        session,
        item,
        WorkItemEventType.COMPLETED,
        telegram_update_id,
        {"from_status": previous, "completed_at": completed_at.isoformat()},
    )
    await cancel_work_item_reminders(session, item, now=completed_at)
    return MutationResult(item, event, True)


async def cancel_work_item(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    telegram_update_id: int,
    *,
    expected_revision: int | None = None,
) -> MutationResult:
    duplicate = await existing_mutation(session, user_id, telegram_update_id)
    if duplicate is not None:
        return duplicate
    item = await lock_work_item(
        session, user_id, work_item_id, expected_revision=expected_revision
    )
    if item.status not in OPEN_STATUSES:
        raise InvalidWorkItemTransitionError("only open work items can be cancelled")
    previous = item.status
    cancelled_at = management_now()
    item.status = WorkItemStatus.CANCELLED.value
    event = await append_management_event(
        session,
        item,
        WorkItemEventType.CANCELLED,
        telegram_update_id,
        {"from_status": previous},
    )
    await cancel_work_item_reminders(session, item, now=cancelled_at)
    return MutationResult(item, event, True)


async def reopen_work_item(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    telegram_update_id: int,
    *,
    expected_revision: int | None = None,
) -> MutationResult:
    duplicate = await existing_mutation(session, user_id, telegram_update_id)
    if duplicate is not None:
        return duplicate
    item = await lock_work_item(
        session, user_id, work_item_id, expected_revision=expected_revision
    )
    if item.status != WorkItemStatus.DONE.value:
        raise InvalidWorkItemTransitionError(
            "only completed work items can be reopened"
        )
    item.status = WorkItemStatus.INBOX.value
    item.completed_at = None
    event = await append_management_event(
        session,
        item,
        WorkItemEventType.REOPENED,
        telegram_update_id,
        {"from_status": WorkItemStatus.DONE.value, "to_status": "inbox"},
    )
    return MutationResult(item, event, True)


async def reschedule_work_item(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    telegram_update_id: int,
    new_date: datetime,
    *,
    reminder_policy: ReminderPolicy | None = None,
    expected_revision: int | None = None,
) -> MutationResult:
    validate_aware_datetime(new_date, "new_date")
    duplicate = await existing_mutation(session, user_id, telegram_update_id)
    if duplicate is not None:
        return duplicate
    item = await lock_work_item(
        session, user_id, work_item_id, expected_revision=expected_revision
    )
    if item.status not in OPEN_STATUSES:
        raise InvalidWorkItemTransitionError("only open work items can be rescheduled")
    field = (
        "next_follow_up_at" if item.type == WorkItemType.FOLLOW_UP.value else "due_at"
    )
    previous = getattr(item, field)
    setattr(item, field, new_date)
    event = await append_management_event(
        session,
        item,
        WorkItemEventType.RESCHEDULED,
        telegram_update_id,
        {
            "field": field,
            "previous": previous.isoformat() if previous is not None else None,
            "new": new_date.isoformat(),
        },
    )
    await sync_work_item_reminders(
        session,
        item,
        policy=reminder_policy,
        allow_final_replacement=previous != new_date,
    )
    return MutationResult(item, event, True)


async def mark_waiting_received(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    telegram_update_id: int,
    *,
    now: datetime | None = None,
    expected_revision: int | None = None,
) -> MutationResult:
    duplicate = await existing_mutation(session, user_id, telegram_update_id)
    if duplicate is not None:
        return duplicate
    item = await lock_work_item(
        session, user_id, work_item_id, expected_revision=expected_revision
    )
    if item.type != WorkItemType.WAITING.value or item.status not in OPEN_STATUSES:
        raise InvalidWorkItemTransitionError("only open waiting items can be received")
    previous = item.status
    received_at = now or management_now()
    item.status = WorkItemStatus.DONE.value
    item.completed_at = received_at
    event = await append_management_event(
        session,
        item,
        WorkItemEventType.WAITING_RECEIVED,
        telegram_update_id,
        {"from_status": previous, "received_at": received_at.isoformat()},
    )
    await cancel_work_item_reminders(session, item, now=received_at)
    return MutationResult(item, event, True)


async def mark_follow_up_replied(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    telegram_update_id: int,
    *,
    now: datetime | None = None,
    expected_revision: int | None = None,
) -> MutationResult:
    duplicate = await existing_mutation(session, user_id, telegram_update_id)
    if duplicate is not None:
        return duplicate
    item = await lock_work_item(
        session, user_id, work_item_id, expected_revision=expected_revision
    )
    if item.type != WorkItemType.FOLLOW_UP.value or item.status not in OPEN_STATUSES:
        raise InvalidWorkItemTransitionError("only open follow-ups can receive replies")
    previous = item.status
    replied_at = now or management_now()
    item.status = WorkItemStatus.DONE.value
    item.completed_at = replied_at
    event = await append_management_event(
        session,
        item,
        WorkItemEventType.PERSON_REPLIED,
        telegram_update_id,
        {"from_status": previous, "replied_at": replied_at.isoformat()},
    )
    await cancel_work_item_reminders(session, item, now=replied_at)
    return MutationResult(item, event, True)


async def archive_work_item(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    telegram_update_id: int,
    *,
    now: datetime | None = None,
) -> MutationResult:
    duplicate = await existing_mutation(session, user_id, telegram_update_id)
    if duplicate is not None:
        return duplicate
    item = await lock_work_item(session, user_id, work_item_id)
    if item.status == WorkItemStatus.ARCHIVED.value:
        raise InvalidWorkItemTransitionError("work item is already archived")
    previous = item.status
    archived_at = now or management_now()
    item.status = WorkItemStatus.ARCHIVED.value
    event = await append_management_event(
        session,
        item,
        WorkItemEventType.ARCHIVED,
        telegram_update_id,
        {"from_status": previous, "archived_at": archived_at.isoformat()},
    )
    await cancel_work_item_reminders(session, item, now=archived_at)
    return MutationResult(item, event, True)


async def add_work_item_note(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    telegram_update_id: int,
    content: str,
    *,
    expected_revision: int | None = None,
) -> tuple[MutationResult, Note]:
    duplicate = await existing_mutation(session, user_id, telegram_update_id)
    if duplicate is not None:
        note_id = duplicate.event.payload.get("note_id")
        note = await session.get(Note, UUID(str(note_id)))
        if note is None:
            raise ValueError("linked note not found")
        return duplicate, note
    item = await lock_work_item(
        session, user_id, work_item_id, expected_revision=expected_revision
    )
    if item.status == WorkItemStatus.ARCHIVED.value:
        raise InvalidWorkItemTransitionError("archived work items cannot be changed")
    note, _ = await create_linked_note(
        session,
        user_id,
        content=content,
        target_type=NoteTargetType.WORK_ITEM,
        target_id=item.id,
    )
    event = await append_management_event(
        session,
        item,
        WorkItemEventType.NOTE_ADDED,
        telegram_update_id,
        {"note_id": str(note.id)},
    )
    return MutationResult(item, event, True), note


async def change_work_item_topic(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    telegram_update_id: int,
    topic_id: UUID | None,
) -> MutationResult:
    duplicate = await existing_mutation(session, user_id, telegram_update_id)
    if duplicate is not None:
        return duplicate
    item = await lock_work_item(session, user_id, work_item_id)
    if item.status == WorkItemStatus.ARCHIVED.value:
        raise InvalidWorkItemTransitionError("archived work items cannot be changed")
    if topic_id is not None and await get_topic(session, user_id, topic_id) is None:
        raise ValueError("topic not found")
    previous = item.topic_id
    item.topic_id = topic_id
    event = await append_management_event(
        session,
        item,
        WorkItemEventType.TOPIC_CHANGED,
        telegram_update_id,
        {
            "previous_topic_id": str(previous) if previous else None,
            "new_topic_id": str(topic_id) if topic_id else None,
        },
    )
    return MutationResult(item, event, True)


async def change_work_item_person(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    telegram_update_id: int,
    person_id: UUID,
    *,
    replace_person_id: UUID | None = None,
) -> MutationResult:
    duplicate = await existing_mutation(session, user_id, telegram_update_id)
    if duplicate is not None:
        return duplicate
    item = await lock_work_item(session, user_id, work_item_id)
    if item.status == WorkItemStatus.ARCHIVED.value:
        raise InvalidWorkItemTransitionError("archived work items cannot be changed")
    if await get_person(session, user_id, person_id) is None:
        raise ValueError("person not found")
    if replace_person_id is not None:
        association = await session.scalar(
            select(WorkItemPerson).where(
                WorkItemPerson.user_id == user_id,
                WorkItemPerson.work_item_id == item.id,
                WorkItemPerson.person_id == replace_person_id,
            )
        )
        if association is None:
            raise ValueError("work item person not found")
        await session.delete(association)
        await session.flush()
    await link_person_to_work_item(session, user_id, item.id, person_id)
    event = await append_management_event(
        session,
        item,
        WorkItemEventType.PERSON_CHANGED,
        telegram_update_id,
        {
            "operation": "replace" if replace_person_id else "add",
            "previous_person_id": str(replace_person_id) if replace_person_id else None,
            "new_person_id": str(person_id),
        },
    )
    return MutationResult(item, event, True)


async def create_follow_up_from_waiting(
    session: AsyncSession,
    user_id: UUID,
    waiting_id: UUID,
    telegram_update_id: int,
    *,
    require_received: bool = True,
    expected_revision: int | None = None,
) -> tuple[WorkItem, bool]:
    duplicate = await event_for_update(session, user_id, telegram_update_id)
    if duplicate is not None:
        return await lock_work_item(session, user_id, duplicate.work_item_id), False
    waiting = await lock_work_item(
        session, user_id, waiting_id, expected_revision=expected_revision
    )
    valid_status = (
        waiting.status == WorkItemStatus.DONE.value
        if require_received
        else waiting.status in (*OPEN_STATUSES, WorkItemStatus.DONE.value)
    )
    if waiting.type != WorkItemType.WAITING.value or not valid_status:
        raise InvalidWorkItemTransitionError("waiting item cannot create a follow-up")
    existing = await session.scalar(
        select(WorkItem)
        .join(WorkItemRelation, WorkItemRelation.source_work_item_id == WorkItem.id)
        .where(
            WorkItem.user_id == user_id,
            WorkItem.type == WorkItemType.FOLLOW_UP.value,
            WorkItemRelation.target_work_item_id == waiting.id,
            WorkItemRelation.relation_type == WorkItemRelationType.CREATED_FROM.value,
        )
    )
    if existing is not None:
        return existing, False
    follow_up = await create_work_item(
        session,
        user_id,
        item_type=WorkItemType.FOLLOW_UP,
        title=f"Follow-up: {waiting.title}",
        topic_id=waiting.topic_id,
        source_note_id=waiting.source_note_id,
        telegram_update_id=telegram_update_id,
    )
    people = list(
        await session.scalars(
            select(WorkItemPerson).where(
                WorkItemPerson.user_id == user_id,
                WorkItemPerson.work_item_id == waiting.id,
            )
        )
    )
    for association in people:
        await link_person_to_work_item(
            session, user_id, follow_up.id, association.person_id
        )
    await create_work_item_relation(
        session,
        user_id,
        follow_up.id,
        waiting.id,
        WorkItemRelationType.CREATED_FROM,
    )
    return follow_up, True
