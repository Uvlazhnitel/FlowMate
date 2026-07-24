from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowmate.ai.prompt_versions import DRAFT_PROMPT_VERSION
from flowmate.ai.schemas import DraftAnalysisResult
from flowmate.db.drafts import replace_draft_analysis, transition_draft
from flowmate.db.models import (
    DraftItemPerson,
    DraftItemRecord,
    DraftSession,
    Meeting,
    MeetingNote,
    MeetingParticipant,
    MeetingTopic,
    Note,
    Person,
    Topic,
)
from flowmate.drafts.questions import next_clarification_question
from flowmate.meetings.enums import MeetingStatus
from flowmate.stabilization.jobs import enqueue_ai_job
from flowmate.task_engine.remaining import (
    DraftItemEdit,
    edit_draft_item,
    serialize_draft,
)


class CaptureConflictError(ValueError):
    """The capture changed or can no longer be modified."""


@dataclass(frozen=True, slots=True)
class CapturePage:
    items: list[dict[str, object]]
    limit: int
    offset: int
    has_more: bool


def capture_now() -> datetime:
    return datetime.now(UTC)


def capture_revision(capture: DraftSession) -> int:
    return int(capture.updated_at.astimezone(UTC).timestamp() * 1_000_000)


async def _meeting_context(
    session: AsyncSession,
    meeting: Meeting,
    *,
    timezone: str,
    captured_at: datetime,
) -> dict[str, object]:
    participant_rows = (
        await session.execute(
            select(Person.id, Person.display_name)
            .join(MeetingParticipant, MeetingParticipant.person_id == Person.id)
            .where(
                MeetingParticipant.user_id == meeting.user_id,
                MeetingParticipant.meeting_id == meeting.id,
                Person.user_id == meeting.user_id,
            )
            .order_by(Person.display_name, Person.id)
        )
    ).all()
    topic_rows = (
        await session.execute(
            select(Topic.id, Topic.name)
            .join(MeetingTopic, MeetingTopic.topic_id == Topic.id)
            .where(
                MeetingTopic.user_id == meeting.user_id,
                MeetingTopic.meeting_id == meeting.id,
                Topic.user_id == meeting.user_id,
            )
            .order_by(Topic.name, Topic.id)
        )
    ).all()
    return {
        "meeting_id": str(meeting.id),
        "meeting_type": meeting.type,
        "workspace": meeting.workspace,
        "timezone": timezone,
        "captured_at": captured_at.isoformat(),
        "participants": [
            {"id": str(person_id), "name": name} for person_id, name in participant_rows
        ],
        "topics": [
            {"id": str(topic_id), "name": name} for topic_id, name in topic_rows
        ],
        "primary_topic_id": (
            str(meeting.primary_topic_id) if meeting.primary_topic_id else None
        ),
    }


async def get_capture_by_note(
    session: AsyncSession, user_id: UUID, note_id: UUID
) -> DraftSession | None:
    return (
        await session.scalars(
            select(DraftSession)
            .options(selectinload(DraftSession.items))
            .where(
                DraftSession.user_id == user_id,
                DraftSession.source_note_id == note_id,
                DraftSession.meeting_id.is_not(None),
            )
        )
    ).one_or_none()


async def create_capture(
    session: AsyncSession,
    *,
    user_id: UUID,
    meeting_id: UUID,
    note: Note,
    timezone: str,
    captured_at: datetime,
    draft_ttl_hours: int,
) -> tuple[DraftSession, bool]:
    if note.user_id != user_id:
        raise ValueError("note not found")
    duplicate = await get_capture_by_note(session, user_id, note.id)
    if duplicate is not None:
        return duplicate, False
    meeting = await session.scalar(
        select(Meeting)
        .where(
            Meeting.id == meeting_id,
            Meeting.user_id == user_id,
            Meeting.status == MeetingStatus.ACTIVE.value,
        )
        .with_for_update()
    )
    if meeting is None:
        raise CaptureConflictError("active meeting not found")
    if note.workspace != meeting.workspace:
        raise ValueError("note not found")
    last_sequence = await session.scalar(
        select(func.max(DraftSession.capture_sequence)).where(
            DraftSession.meeting_id == meeting.id,
            DraftSession.user_id == user_id,
        )
    )
    capture = DraftSession(
        user_id=user_id,
        source_note_id=note.id,
        meeting_id=meeting.id,
        capture_sequence=int(last_sequence or 0) + 1,
        capture_review_status="pending",
        capture_context=await _meeting_context(
            session, meeting, timezone=timezone, captured_at=captured_at
        ),
        status="parsing",
        prompt_version=DRAFT_PROMPT_VERSION,
        expires_at=captured_at + timedelta(hours=draft_ttl_hours),
        updated_at=captured_at,
    )
    session.add(capture)
    linked = await session.scalar(
        select(MeetingNote).where(MeetingNote.note_id == note.id)
    )
    if linked is None:
        session.add(
            MeetingNote(user_id=user_id, meeting_id=meeting.id, note_id=note.id)
        )
    await session.flush()
    await enqueue_ai_job(
        session,
        user_id=user_id,
        job_kind="meeting_capture_parse",
        entity_id=capture.id,
        operation_key="initial",
        prompt_name="draft",
        prompt_version=DRAFT_PROMPT_VERSION,
        input_source=note.source,
        now=captured_at,
    )
    return capture, True


def _unique_name_map(values: list[dict[str, Any]]) -> dict[str, UUID]:
    grouped: dict[str, list[UUID]] = {}
    for value in values:
        grouped.setdefault(str(value["name"]).strip().casefold(), []).append(
            UUID(str(value["id"]))
        )
    return {name: ids[0] for name, ids in grouped.items() if len(ids) == 1}


async def resolve_capture_item_context(
    session: AsyncSession, capture: DraftSession, item: DraftItemRecord
) -> None:
    people = _unique_name_map(list(capture.capture_context.get("participants", [])))
    topics = _unique_name_map(list(capture.capture_context.get("topics", [])))
    await session.execute(
        delete(DraftItemPerson).where(DraftItemPerson.draft_item_id == item.id)
    )
    selected_people = {
        people[name.strip().casefold()]
        for name in item.people_candidates
        if name.strip().casefold() in people
    }
    for person_id in selected_people:
        session.add(
            DraftItemPerson(
                user_id=capture.user_id,
                draft_item_id=item.id,
                person_id=person_id,
            )
        )
    selected_topics = {
        topics[name.strip().casefold()]
        for name in item.topic_candidates
        if name.strip().casefold() in topics
    }
    item.selected_topic_id = (
        next(iter(selected_topics)) if len(selected_topics) == 1 else None
    )
    await session.flush()


async def _resolve_exact_context(session: AsyncSession, capture: DraftSession) -> None:
    for item in capture.items:
        await resolve_capture_item_context(session, capture, item)


async def save_capture_analysis(
    session: AsyncSession,
    capture: DraftSession,
    analysis: DraftAnalysisResult,
    *,
    draft_ttl_hours: int,
    now: datetime | None = None,
) -> DraftSession:
    if capture.meeting_id is None or capture.capture_review_status == "removed":
        raise CaptureConflictError("capture can no longer be analyzed")
    timestamp = now or capture_now()
    question = next_clarification_question(analysis)
    await replace_draft_analysis(
        session,
        capture,
        analysis,
        question=question,
        ttl_hours=draft_ttl_hours,
        now=timestamp,
    )
    capture.overall_confidence = analysis.confidence
    capture.updated_at = timestamp
    await _resolve_exact_context(session, capture)
    return capture


async def mark_capture_failed(
    session: AsyncSession, capture: DraftSession, *, now: datetime | None = None
) -> None:
    await transition_draft(session, capture, "failed")
    capture.updated_at = now or capture_now()
    await session.flush()


async def get_owned_capture(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    capture_id: UUID,
    *,
    for_update: bool = False,
) -> DraftSession | None:
    statement = (
        select(DraftSession)
        .options(selectinload(DraftSession.items))
        .where(
            DraftSession.id == capture_id,
            DraftSession.user_id == user_id,
            DraftSession.meeting_id == meeting_id,
        )
    )
    if for_update:
        statement = statement.with_for_update()
    return (await session.scalars(statement)).one_or_none()


async def serialize_capture(
    session: AsyncSession, capture: DraftSession
) -> dict[str, object]:
    note = await session.scalar(
        select(Note).where(
            Note.id == capture.source_note_id, Note.user_id == capture.user_id
        )
    )
    payload = await serialize_draft(
        session,
        capture,
        source_content=(note.content or "") if note is not None else "",
    )
    return {
        **payload,
        "meeting_id": capture.meeting_id,
        "sequence": capture.capture_sequence,
        "review_status": capture.capture_review_status,
        "context": capture.capture_context,
        "confidence": capture.overall_confidence,
        "suggested_question": capture.current_question,
        "source_type": note.source if note is not None else "text",
        "source_text": note.content if note is not None else None,
        "source_redacted": bool(
            note is not None and note.transcript_redacted_at is not None
        ),
    }


async def list_captures(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    *,
    limit: int,
    offset: int,
) -> CapturePage:
    if not 1 <= limit <= 50 or offset < 0:
        raise ValueError("invalid pagination")
    meeting = await session.scalar(
        select(Meeting.id).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
    )
    if meeting is None:
        raise ValueError("meeting not found")
    rows = list(
        await session.scalars(
            select(DraftSession)
            .options(selectinload(DraftSession.items))
            .where(
                DraftSession.user_id == user_id,
                DraftSession.meeting_id == meeting_id,
                DraftSession.capture_review_status != "removed",
            )
            .order_by(DraftSession.capture_sequence, DraftSession.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    return CapturePage(
        [await serialize_capture(session, value) for value in rows[:limit]],
        limit,
        offset,
        len(rows) > limit,
    )


async def edit_capture_item(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    capture_id: UUID,
    item_id: UUID,
    edit: DraftItemEdit,
    *,
    expected_revision: int,
    high_threshold: float,
    clarification_threshold: float,
    draft_ttl_hours: int,
    now: datetime,
) -> DraftSession:
    meeting = await session.scalar(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == user_id,
            Meeting.status.in_(
                (MeetingStatus.ACTIVE.value, MeetingStatus.REVIEW_REQUIRED.value)
            ),
        )
    )
    capture = await get_owned_capture(
        session, user_id, meeting_id, capture_id, for_update=True
    )
    if meeting is None or capture is None or capture.capture_review_status == "removed":
        raise CaptureConflictError("capture cannot be edited")
    updated = await edit_draft_item(
        session,
        user_id,
        capture_id,
        item_id,
        edit,
        expected_revision=expected_revision,
        high_threshold=high_threshold,
        clarification_threshold=clarification_threshold,
        ttl_hours=draft_ttl_hours,
        now=now,
        meeting_id=meeting_id,
    )
    updated.capture_review_status = "edited"
    updated.current_question = None
    updated.updated_at = now
    await session.flush()
    await session.refresh(updated, attribute_names=["updated_at"])
    return updated


async def remove_capture(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    capture_id: UUID,
    *,
    expected_revision: int | None = None,
    latest_only: bool = False,
    now: datetime | None = None,
) -> DraftSession:
    meeting = await session.scalar(
        select(Meeting).where(
            Meeting.id == meeting_id,
            Meeting.user_id == user_id,
            Meeting.status.in_(
                (MeetingStatus.ACTIVE.value, MeetingStatus.REVIEW_REQUIRED.value)
            ),
        )
    )
    capture = await get_owned_capture(
        session, user_id, meeting_id, capture_id, for_update=True
    )
    if meeting is None or capture is None:
        raise CaptureConflictError("capture cannot be removed")
    if capture.capture_review_status == "removed":
        return capture
    if expected_revision is not None and capture_revision(capture) != expected_revision:
        raise CaptureConflictError("capture is stale")
    if latest_only:
        latest = await session.scalar(
            select(func.max(DraftSession.capture_sequence)).where(
                DraftSession.user_id == user_id,
                DraftSession.meeting_id == meeting_id,
                DraftSession.capture_review_status != "removed",
            )
        )
        if capture.capture_sequence != latest:
            raise CaptureConflictError("only the latest capture can be undone")
    capture.capture_review_status = "removed"
    capture.status = "cancelled"
    capture.current_question = None
    capture.updated_at = now or capture_now()
    await session.flush()
    return capture
