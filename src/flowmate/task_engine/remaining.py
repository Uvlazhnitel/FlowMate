import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowmate.ai.analysis import classify_readiness
from flowmate.ai.schemas import (
    DraftItemAssessment,
    DraftItemType,
    DraftReadiness,
    TemporalCandidate,
    TemporalStatus,
)
from flowmate.db.models import (
    DraftItemPerson,
    DraftSession,
    Meeting,
    MeetingEvent,
    MeetingParticipant,
    MeetingReview,
    MeetingReviewItem,
    MeetingTopic,
    Note,
    NoteLink,
    Person,
    Topic,
    WorkItem,
    WorkItemEvent,
    WorkItemPerson,
)
from flowmate.task_engine.enums import PlannerStatus, WorkItemPriority, WorkItemType
from flowmate.task_engine.operational import PageResult, build_work_item_cards
from flowmate.task_engine.planner import ELIGIBLE_PLANNER_TYPES
from flowmate.task_engine.queries import OPEN_STATUSES, validate_pagination
from flowmate.task_engine.service import (
    get_person,
    get_topic,
    normalize_optional_text,
    normalize_required_text,
)

InboxKind = Literal["draft", "work_item", "note", "meeting_review"]
TimelineEventType = Literal[
    "created",
    "converted_from_draft",
    "completed",
    "reopened",
    "cancelled",
    "rescheduled",
    "snoozed",
    "note_added",
    "topic_changed",
    "person_changed",
    "waiting_received",
    "planner_status_changed",
    "archived",
    "meeting_created",
    "meeting_started",
    "meeting_ended",
    "meeting_cancelled",
    "meeting_review_generated",
    "meeting_review_failed",
    "meeting_clarification_answered",
    "meeting_converted",
    "meeting_completed",
    "meeting_agenda_updated",
]


@dataclass(frozen=True, slots=True)
class DraftItemEdit:
    item_type: DraftItemType
    title: str
    description: str | None
    priority: WorkItemPriority
    topic_id: UUID | None
    person_ids: tuple[UUID, ...]
    due_at: datetime | None


def _draft_revision(draft: DraftSession) -> int:
    return int(draft.updated_at.astimezone(UTC).timestamp() * 1_000_000)


async def _draft_people(
    session: AsyncSession, user_id: UUID, item_ids: list[UUID]
) -> dict[UUID, list[dict[str, object]]]:
    values: dict[UUID, list[dict[str, object]]] = {item_id: [] for item_id in item_ids}
    if not item_ids:
        return values
    rows = await session.execute(
        select(DraftItemPerson.draft_item_id, Person.id, Person.display_name)
        .join(Person, Person.id == DraftItemPerson.person_id)
        .where(
            DraftItemPerson.user_id == user_id,
            DraftItemPerson.draft_item_id.in_(item_ids),
            Person.user_id == user_id,
        )
        .order_by(Person.display_name, Person.id)
    )
    for item_id, person_id, name in rows:
        values[item_id].append({"id": person_id, "display_name": name})
    return values


async def serialize_draft(
    session: AsyncSession, draft: DraftSession, *, source_content: str
) -> dict[str, object]:
    people = await _draft_people(
        session, draft.user_id, [item.id for item in draft.items]
    )
    topic_ids = {
        item.selected_topic_id for item in draft.items if item.selected_topic_id
    }
    topics = {
        topic.id: topic
        for topic in await session.scalars(
            select(Topic).where(Topic.user_id == draft.user_id, Topic.id.in_(topic_ids))
        )
    }
    return {
        "id": draft.id,
        "kind": "draft",
        "status": draft.status,
        "revision": _draft_revision(draft),
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
        "expires_at": draft.expires_at,
        "recoverable": bool(draft.items and draft.analysis_payload),
        "source_excerpt": source_content[:500],
        "items": [
            {
                "id": item.id,
                "position": item.position,
                "type": item.item_type,
                "title": item.title,
                "description": item.description,
                "priority": item.selected_priority,
                "confidence": item.confidence,
                "readiness": item.readiness,
                "missing_fields": item.missing_fields,
                "ambiguities": item.ambiguities,
                "due_at": item.normalized_date,
                "topic": (
                    {
                        "id": topics[item.selected_topic_id].id,
                        "name": topics[item.selected_topic_id].name,
                    }
                    if item.selected_topic_id in topics
                    else None
                ),
                "people": people[item.id],
            }
            for item in draft.items
        ],
    }


def _work_item_reasons(item: Any) -> list[str]:
    reasons: list[str] = []
    if item.status == "inbox":
        reasons.append("inbox_status")
    if item.type == WorkItemType.TASK.value:
        if item.due_at is None:
            reasons.append("missing_date")
        if item.topic_id is None:
            reasons.append("missing_topic")
    elif item.type == WorkItemType.FOLLOW_UP.value:
        if item.next_follow_up_at is None and item.due_at is None:
            reasons.append("missing_date")
        if not item.people:
            reasons.append("missing_person")
    elif (
        item.type
        in {
            WorkItemType.WAITING.value,
            WorkItemType.QUESTION.value,
            WorkItemType.AGENDA_ITEM.value,
        }
        and not item.people
    ):
        reasons.append("missing_person")
    elif item.type == WorkItemType.DECISION.value and item.topic_id is None:
        reasons.append("missing_topic")
    return reasons


async def list_inbox(
    session: AsyncSession,
    user_id: UUID,
    *,
    now: datetime,
    low_confidence_threshold: float,
    kind: InboxKind | None,
    reason: str | None,
    limit: int,
    offset: int,
) -> PageResult:
    validate_pagination(limit, offset)
    if limit > 50:
        raise ValueError("limit must not exceed 50")
    entries: list[tuple[datetime, str, dict[str, object]]] = []
    if kind in {None, "draft"}:
        drafts = list(
            await session.scalars(
                select(DraftSession)
                .options(selectinload(DraftSession.items))
                .where(
                    DraftSession.user_id == user_id,
                    DraftSession.meeting_id.is_(None),
                    DraftSession.status.in_(
                        ("parsing", "needs_clarification", "ready", "expired", "failed")
                    ),
                )
                .order_by(DraftSession.updated_at.desc(), DraftSession.id)
            )
        )
        note_by_id = {
            note.id: note
            for note in await session.scalars(
                select(Note).where(
                    Note.user_id == user_id,
                    Note.id.in_([draft.source_note_id for draft in drafts]),
                )
            )
        }
        for draft in drafts:
            effective_status = (
                "expired"
                if draft.status in {"parsing", "needs_clarification", "ready"}
                and draft.expires_at <= now
                else draft.status
            )
            reasons = {"unresolved_draft"}
            if effective_status in {"expired", "failed"}:
                reasons.add("interrupted")
            if any(item.confidence < low_confidence_threshold for item in draft.items):
                reasons.add("low_confidence")
            if any(
                item.readiness != DraftReadiness.READY.value for item in draft.items
            ):
                reasons.add("incomplete")
            if reason is not None and reason not in reasons:
                continue
            source = note_by_id.get(draft.source_note_id)
            payload = await serialize_draft(
                session, draft, source_content=(source.content or "") if source else ""
            )
            payload["status"] = effective_status
            payload["reasons"] = sorted(reasons)
            entries.append((draft.updated_at, str(draft.id), payload))

    if kind in {None, "work_item"}:
        work_items = list(
            await session.scalars(
                select(WorkItem)
                .where(WorkItem.user_id == user_id, WorkItem.status.in_(OPEN_STATUSES))
                .order_by(WorkItem.updated_at.desc(), WorkItem.id)
            )
        )
        cards = await build_work_item_cards(session, user_id, work_items, now=now)
        for card in cards:
            work_reasons = _work_item_reasons(card)
            if not work_reasons or (reason is not None and reason not in work_reasons):
                continue
            payload = {
                "kind": "work_item",
                "reasons": work_reasons,
                "item": card,
            }
            entries.append((card.updated_at, str(card.id), payload))

    if kind in {None, "note"}:
        notes = list(
            await session.scalars(
                select(Note)
                .where(
                    Note.user_id == user_id,
                    Note.inbox_disposition == "pending",
                    ~exists().where(DraftSession.source_note_id == Note.id),
                    ~exists().where(NoteLink.note_id == Note.id),
                )
                .order_by(Note.created_at.desc(), Note.id)
            )
        )
        if reason in {None, "unstructured_note"}:
            for note in notes:
                entries.append(
                    (
                        note.created_at,
                        str(note.id),
                        {
                            "id": note.id,
                            "kind": "note",
                            "reasons": ["unstructured_note"],
                            "excerpt": (note.content or "")[:500],
                            "source": note.source,
                            "created_at": note.created_at,
                        },
                    )
                )

    if kind in {None, "meeting_review"} and reason in {None, "meeting_review"}:
        review_rows = list(
            (
                await session.execute(
                    select(MeetingReviewItem, MeetingReview, Meeting)
                    .join(
                        MeetingReview, MeetingReview.id == MeetingReviewItem.review_id
                    )
                    .join(Meeting, Meeting.id == MeetingReview.meeting_id)
                    .where(
                        MeetingReviewItem.user_id == user_id,
                        MeetingReview.user_id == user_id,
                        Meeting.user_id == user_id,
                        MeetingReviewItem.status == "inbox",
                    )
                    .order_by(MeetingReviewItem.updated_at.desc(), MeetingReviewItem.id)
                )
            ).all()
        )
        for review_item, review, meeting in review_rows:
            entries.append(
                (
                    review_item.updated_at,
                    str(review_item.id),
                    {
                        "id": review_item.id,
                        "kind": "meeting_review",
                        "reasons": ["meeting_review"],
                        "meeting_id": meeting.id,
                        "meeting_title": meeting.title,
                        "review_id": review.id,
                        "category": review_item.category,
                        "title": review_item.title,
                        "created_at": review_item.created_at,
                    },
                )
            )

    entries.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    page = entries[offset : offset + limit + 1]
    return PageResult(
        items=[entry[2] for entry in page[:limit]],
        limit=limit,
        offset=offset,
        has_more=len(page) > limit,
    )


async def get_owned_draft(
    session: AsyncSession,
    user_id: UUID,
    draft_id: UUID,
    *,
    for_update: bool = False,
    meeting_id: UUID | None = None,
) -> DraftSession | None:
    statement = (
        select(DraftSession)
        .options(selectinload(DraftSession.items))
        .where(
            DraftSession.id == draft_id,
            DraftSession.user_id == user_id,
            (
                DraftSession.meeting_id.is_(None)
                if meeting_id is None
                else DraftSession.meeting_id == meeting_id
            ),
        )
    )
    if for_update:
        statement = statement.with_for_update()
    return (await session.scalars(statement)).one_or_none()


async def edit_draft_item(
    session: AsyncSession,
    user_id: UUID,
    draft_id: UUID,
    item_id: UUID,
    edit: DraftItemEdit,
    *,
    expected_revision: int,
    high_threshold: float,
    clarification_threshold: float,
    ttl_hours: int,
    now: datetime,
    meeting_id: UUID | None = None,
) -> DraftSession:
    draft = await get_owned_draft(
        session,
        user_id,
        draft_id,
        for_update=True,
        meeting_id=meeting_id,
    )
    if draft is None:
        raise ValueError("draft not found")
    if draft.status in {"confirmed", "cancelled"}:
        raise ValueError("draft can no longer be edited")
    if _draft_revision(draft) != expected_revision:
        raise ValueError("draft is stale")
    record = next((item for item in draft.items if item.id == item_id), None)
    if record is None:
        raise ValueError("draft item not found")
    if edit.item_type is DraftItemType.UNKNOWN:
        raise ValueError("unknown is not a selectable draft type")
    selected_topic = (
        await get_topic(session, user_id, edit.topic_id)
        if edit.topic_id is not None
        else None
    )
    if edit.topic_id is not None and selected_topic is None:
        raise ValueError("topic not found")
    selected_people: list[Person] = []
    for person_id in dict.fromkeys(edit.person_ids):
        person = await get_person(session, user_id, person_id)
        if person is None:
            raise ValueError("person not found")
        selected_people.append(person)
    try:
        current = DraftItemAssessment.model_validate_json(
            json.dumps(record.raw_payload)
        )
    except ValueError as error:
        raise ValueError("draft item payload is invalid") from error
    temporal = (
        TemporalCandidate(
            original_phrase="Set in FlowMate",
            normalized_value=edit.due_at,
            status=TemporalStatus.RESOLVED,
            explanation=None,
            time_was_explicit=True,
        )
        if edit.due_at is not None
        else None
    )
    updated_item = current.item.model_copy(
        update={
            "type": edit.item_type,
            "title": normalize_required_text(edit.title, "title"),
            "description": normalize_optional_text(edit.description),
            "person_candidates": [person.display_name for person in selected_people],
            "topic_candidates": [selected_topic.name] if selected_topic else [],
            "due_date_candidate": temporal,
            "missing_fields": [],
            "ambiguities": [],
        }
    )
    readiness = classify_readiness(
        updated_item,
        result_ambiguities=[],
        high_threshold=high_threshold,
        clarification_threshold=clarification_threshold,
    )
    assessment = DraftItemAssessment(item=updated_item, readiness=readiness)
    record.item_type = edit.item_type.value
    record.title = updated_item.title
    record.description = updated_item.description
    record.people_candidates = updated_item.person_candidates
    record.topic_candidates = updated_item.topic_candidates
    record.original_date_text = temporal.original_phrase if temporal else None
    record.normalized_date = edit.due_at
    record.missing_fields = []
    record.ambiguities = []
    record.readiness = readiness.value
    record.raw_payload = assessment.model_dump(mode="json")
    record.selected_topic_id = edit.topic_id
    record.selected_priority = edit.priority.value
    await session.execute(
        delete(DraftItemPerson).where(DraftItemPerson.draft_item_id == record.id)
    )
    for person in selected_people:
        session.add(
            DraftItemPerson(
                id=uuid4(),
                user_id=user_id,
                draft_item_id=record.id,
                person_id=person.id,
            )
        )
    if draft.analysis_payload is not None:
        items = list(draft.analysis_payload.get("items", []))
        if record.position <= len(items):
            items[record.position - 1] = assessment.model_dump(mode="json")
            draft.analysis_payload = {**draft.analysis_payload, "items": items}
    draft.status = (
        "ready"
        if all(item.readiness == DraftReadiness.READY.value for item in draft.items)
        else "needs_clarification"
    )
    draft.current_question = None
    draft.current_question_options = []
    draft.current_question_context = None
    draft.current_question_message_id = None
    draft.expires_at = now + timedelta(hours=ttl_hours)
    draft.updated_at = now
    await session.flush()
    return draft


async def list_planner_queue(
    session: AsyncSession,
    user_id: UUID,
    *,
    statuses: tuple[PlannerStatus, ...],
    query: str | None,
    now: datetime,
    limit: int,
    offset: int,
) -> PageResult:
    validate_pagination(limit, offset)
    if limit > 50:
        raise ValueError("limit must not exceed 50")
    statement = select(WorkItem).where(
        WorkItem.user_id == user_id,
        WorkItem.type.in_(ELIGIBLE_PLANNER_TYPES),
        WorkItem.planner_status.in_([status.value for status in statuses]),
    )
    if query and query.strip():
        statement = statement.where(WorkItem.title.ilike(f"%{query.strip()}%"))
    values = list(
        await session.scalars(
            statement.order_by(WorkItem.updated_at.desc(), WorkItem.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    cards = await build_work_item_cards(session, user_id, values[:limit], now=now)
    by_id = {item.id: item for item in values[:limit]}
    return PageResult(
        items=[
            {
                "item": card,
                "planner_status": by_id[card.id].planner_status,
                "transferred_at": by_id[card.id].planner_transferred_at,
            }
            for card in cards
        ],
        limit=limit,
        offset=offset,
        has_more=len(values) > limit,
    )


def _public_event_type(event: WorkItemEvent, item: WorkItem) -> str:
    if event.event_type == "created" and item.source_draft_item_id is not None:
        return "converted_from_draft"
    if event.event_type == "reminder_snoozed":
        return "snoozed"
    return event.event_type


async def list_timeline(
    session: AsyncSession,
    user_id: UUID,
    *,
    start: datetime | None,
    end: datetime | None,
    topic_id: UUID | None,
    person_id: UUID | None,
    event_type: str | None,
    work_item_type: WorkItemType | None,
    limit: int,
    offset: int,
) -> PageResult:
    validate_pagination(limit, offset)
    if limit > 100:
        raise ValueError("limit must not exceed 100")
    include_work_items = event_type is None or not event_type.startswith("meeting_")
    include_meetings = work_item_type is None and (
        event_type is None or event_type.startswith("meeting_")
    )
    statement = (
        select(WorkItemEvent, WorkItem, Topic)
        .join(WorkItem, WorkItem.id == WorkItemEvent.work_item_id)
        .outerjoin(Topic, Topic.id == WorkItem.topic_id)
        .where(WorkItemEvent.user_id == user_id, WorkItem.user_id == user_id)
    )
    if start is not None:
        statement = statement.where(WorkItemEvent.created_at >= start)
    if end is not None:
        statement = statement.where(WorkItemEvent.created_at < end)
    if topic_id is not None:
        statement = statement.where(WorkItem.topic_id == topic_id)
    if person_id is not None:
        statement = statement.where(
            exists().where(
                WorkItemPerson.work_item_id == WorkItem.id,
                WorkItemPerson.person_id == person_id,
                WorkItemPerson.user_id == user_id,
            )
        )
    if work_item_type is not None:
        statement = statement.where(WorkItem.type == work_item_type.value)
    if event_type == "converted_from_draft":
        statement = statement.where(
            WorkItemEvent.event_type == "created",
            WorkItem.source_draft_item_id.is_not(None),
        )
    elif event_type == "created":
        statement = statement.where(
            WorkItemEvent.event_type == "created",
            WorkItem.source_draft_item_id.is_(None),
        )
    elif event_type == "snoozed":
        statement = statement.where(WorkItemEvent.event_type == "reminder_snoozed")
    elif event_type is not None:
        statement = statement.where(WorkItemEvent.event_type == event_type)
    fetch_limit = offset + limit + 1
    work_rows = (
        list(
            (
                await session.execute(
                    statement.order_by(
                        WorkItemEvent.created_at.desc(), WorkItemEvent.id.desc()
                    ).limit(fetch_limit)
                )
            ).all()
        )
        if include_work_items
        else []
    )

    meeting_statement = (
        select(MeetingEvent, Meeting)
        .join(Meeting, Meeting.id == MeetingEvent.meeting_id)
        .where(MeetingEvent.user_id == user_id, Meeting.user_id == user_id)
    )
    if start is not None:
        meeting_statement = meeting_statement.where(MeetingEvent.created_at >= start)
    if end is not None:
        meeting_statement = meeting_statement.where(MeetingEvent.created_at < end)
    if topic_id is not None:
        meeting_statement = meeting_statement.where(
            exists().where(
                MeetingTopic.meeting_id == Meeting.id,
                MeetingTopic.topic_id == topic_id,
                MeetingTopic.user_id == user_id,
            )
        )
    if person_id is not None:
        meeting_statement = meeting_statement.where(
            exists().where(
                MeetingParticipant.meeting_id == Meeting.id,
                MeetingParticipant.person_id == person_id,
                MeetingParticipant.user_id == user_id,
            )
        )
    if event_type is not None and event_type.startswith("meeting_"):
        meeting_statement = meeting_statement.where(
            MeetingEvent.event_type == event_type.removeprefix("meeting_")
        )
    meeting_rows = (
        list(
            (
                await session.execute(
                    meeting_statement.order_by(
                        MeetingEvent.created_at.desc(), MeetingEvent.id.desc()
                    ).limit(fetch_limit)
                )
            ).all()
        )
        if include_meetings
        else []
    )

    combined = [
        (event.created_at, event.id, "work_item", event, item, topic)
        for event, item, topic in work_rows
    ] + [
        (event.created_at, event.id, "meeting", event, meeting, None)
        for event, meeting in meeting_rows
    ]
    combined.sort(key=lambda value: (value[0], value[1]), reverse=True)
    rows = combined[offset : offset + limit + 1]
    visible = rows[:limit]
    item_ids = {
        entity.id for _, _, kind, _, entity, _ in visible if kind == "work_item"
    }
    meeting_ids = {
        entity.id for _, _, kind, _, entity, _ in visible if kind == "meeting"
    }
    people: dict[UUID, list[dict[str, object]]] = {item_id: [] for item_id in item_ids}
    if item_ids:
        person_rows = await session.execute(
            select(WorkItemPerson.work_item_id, Person.id, Person.display_name)
            .join(Person, Person.id == WorkItemPerson.person_id)
            .where(
                WorkItemPerson.user_id == user_id,
                WorkItemPerson.work_item_id.in_(item_ids),
            )
            .order_by(Person.display_name, Person.id)
        )
        for item_id, linked_id, name in person_rows:
            people[item_id].append({"id": linked_id, "display_name": name})
    meeting_people: dict[UUID, list[dict[str, object]]] = {
        meeting_id: [] for meeting_id in meeting_ids
    }
    meeting_topics: dict[UUID, list[dict[str, object]]] = {
        meeting_id: [] for meeting_id in meeting_ids
    }
    if meeting_ids:
        participant_rows = await session.execute(
            select(MeetingParticipant.meeting_id, Person.id, Person.display_name)
            .join(Person, Person.id == MeetingParticipant.person_id)
            .where(
                MeetingParticipant.user_id == user_id,
                MeetingParticipant.meeting_id.in_(meeting_ids),
                Person.user_id == user_id,
            )
            .order_by(Person.display_name, Person.id)
        )
        for meeting_id, linked_id, name in participant_rows:
            meeting_people[meeting_id].append({"id": linked_id, "display_name": name})
        topic_rows = await session.execute(
            select(MeetingTopic.meeting_id, Topic.id, Topic.name)
            .join(Topic, Topic.id == MeetingTopic.topic_id)
            .where(
                MeetingTopic.user_id == user_id,
                MeetingTopic.meeting_id.in_(meeting_ids),
                Topic.user_id == user_id,
            )
            .order_by(Topic.name, Topic.id)
        )
        for meeting_id, linked_id, name in topic_rows:
            meeting_topics[meeting_id].append({"id": linked_id, "name": name})

    serialized: list[dict[str, object]] = []
    for _, _, kind, event, entity, topic in visible:
        if kind == "work_item":
            serialized.append(
                {
                    "id": event.id,
                    "entity_kind": "work_item",
                    "entity_id": entity.id,
                    "event_type": _public_event_type(event, entity),
                    "occurred_at": event.created_at,
                    "title": entity.title,
                    "work_item_type": entity.type,
                    "status": entity.status,
                    "topics": (
                        [{"id": topic.id, "name": topic.name}]
                        if topic is not None
                        else []
                    ),
                    "people": people[entity.id],
                }
            )
        else:
            serialized.append(
                {
                    "id": event.id,
                    "entity_kind": "meeting",
                    "entity_id": entity.id,
                    "event_type": f"meeting_{event.event_type}",
                    "occurred_at": event.created_at,
                    "title": entity.title,
                    "work_item_type": None,
                    "status": event.new_status,
                    "topics": meeting_topics[entity.id],
                    "people": meeting_people[entity.id],
                }
            )
    return PageResult(
        items=serialized,
        limit=limit,
        offset=offset,
        has_more=len(rows) > limit,
    )
