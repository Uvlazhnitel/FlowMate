import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowmate.ai.analysis import classify_readiness
from flowmate.ai.provider import MeetingReviewProvider
from flowmate.ai.schemas import (
    DraftItem,
    DraftItemAssessment,
    DraftItemType,
    DraftReadiness,
    MeetingReviewParseResult,
)
from flowmate.db.models import (
    DraftItemRecord,
    DraftSession,
    Meeting,
    MeetingAgendaEntry,
    MeetingEvent,
    MeetingParticipant,
    MeetingReview,
    MeetingReviewItem,
    MeetingTopic,
    MeetingWorkItem,
    Note,
    WorkItem,
    WorkItemPerson,
)
from flowmate.meetings.enums import MeetingEventType, MeetingStatus
from flowmate.task_engine.conversion import DraftConversionService
from flowmate.task_engine.enums import (
    PlannerStatus,
    WorkItemRelationType,
    WorkItemStatus,
    WorkItemType,
)
from flowmate.task_engine.management import bind_client_action, complete_work_item
from flowmate.task_engine.queries import OPEN_STATUSES
from flowmate.task_engine.service import create_work_item, create_work_item_relation


class MeetingReviewError(ValueError):
    """Meeting review generation or transition failed safely."""


@dataclass(frozen=True, slots=True)
class ReviewConfirmation:
    converted_ids: tuple[UUID, ...]
    note_ids: tuple[UUID, ...]
    inbox_count: int
    completed: bool


CATEGORY_TYPES = {
    "task": DraftItemType.TASK,
    "follow_up": DraftItemType.FOLLOW_UP,
    "waiting": DraftItemType.WAITING,
    "answered_question": DraftItemType.QUESTION,
    "unresolved_question": DraftItemType.QUESTION,
    "note": DraftItemType.NOTE,
    "decision": DraftItemType.DECISION,
    "agenda_item": DraftItemType.AGENDA_ITEM,
}


def review_now() -> datetime:
    return datetime.now(UTC)


def review_revision(review: MeetingReview) -> int:
    return int(review.updated_at.astimezone(UTC).timestamp() * 1_000_000)


async def get_review(
    session: AsyncSession, user_id: UUID, meeting_id: UUID, *, for_update: bool = False
) -> MeetingReview | None:
    statement = select(MeetingReview).where(
        MeetingReview.user_id == user_id, MeetingReview.meeting_id == meeting_id
    )
    if for_update:
        statement = statement.with_for_update()
    return (await session.scalars(statement)).first()


async def sync_review_capture_item(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    draft_item_id: UUID,
) -> None:
    """Keep an editable review projection aligned with its source draft item."""
    review = await get_review(session, user_id, meeting_id, for_update=True)
    if review is None or review.status != "review_required":
        return
    item = await session.scalar(
        select(MeetingReviewItem).where(
            MeetingReviewItem.review_id == review.id,
            MeetingReviewItem.user_id == user_id,
            MeetingReviewItem.source_draft_item_id == draft_item_id,
        )
    )
    record = await session.scalar(
        select(DraftItemRecord)
        .join(DraftSession, DraftSession.id == DraftItemRecord.draft_session_id)
        .where(
            DraftItemRecord.id == draft_item_id,
            DraftSession.user_id == user_id,
            DraftSession.meeting_id == meeting_id,
        )
    )
    if item is None or record is None:
        return
    assessment = DraftItemAssessment.model_validate_json(json.dumps(record.raw_payload))
    item.title = record.title
    item.raw_payload = record.raw_payload
    if CATEGORY_TYPES[item.category] is not assessment.item.type:
        item.category = {
            DraftItemType.TASK: "task",
            DraftItemType.FOLLOW_UP: "follow_up",
            DraftItemType.WAITING: "waiting",
            DraftItemType.QUESTION: "unresolved_question",
            DraftItemType.NOTE: "note",
            DraftItemType.DECISION: "decision",
            DraftItemType.AGENDA_ITEM: "agenda_item",
            DraftItemType.UNKNOWN: "note",
        }[assessment.item.type]
    if item.status not in {"excluded", "inbox", "converted"}:
        item.status = (
            "ready"
            if assessment.readiness is DraftReadiness.READY
            and assessment.item.type is not DraftItemType.UNKNOWN
            else "clarification_required"
        )
    review.updated_at = review_now()
    await session.flush()


async def sync_removed_review_capture(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    capture_id: UUID,
) -> None:
    review = await get_review(session, user_id, meeting_id, for_update=True)
    if review is None or review.status != "review_required":
        return
    items = list(
        await session.scalars(
            select(MeetingReviewItem).where(
                MeetingReviewItem.review_id == review.id,
                MeetingReviewItem.user_id == user_id,
                MeetingReviewItem.source_capture_id == capture_id,
                MeetingReviewItem.status.in_(
                    ("pending", "ready", "clarification_required")
                ),
            )
        )
    )
    for item in items:
        item.status = "excluded"
    if items:
        review.updated_at = review_now()
        await session.flush()


async def get_latest_review(
    session: AsyncSession, user_id: UUID
) -> MeetingReview | None:
    return (
        await session.scalars(
            select(MeetingReview)
            .where(
                MeetingReview.user_id == user_id,
                MeetingReview.status.in_(("review_required", "failed", "completed")),
            )
            .order_by(MeetingReview.updated_at.desc(), MeetingReview.id.desc())
            .limit(1)
        )
    ).first()


async def ensure_review(
    session: AsyncSession, user_id: UUID, meeting_id: UUID
) -> MeetingReview:
    meeting = await session.scalar(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
    )
    if meeting is None or meeting.status not in {
        MeetingStatus.PROCESSING.value,
        MeetingStatus.REVIEW_REQUIRED.value,
        MeetingStatus.COMPLETED.value,
    }:
        raise MeetingReviewError("meeting is not ready for review")
    review = await get_review(session, user_id, meeting_id, for_update=True)
    if review is None:
        review = MeetingReview(
            user_id=user_id, meeting_id=meeting_id, status="processing"
        )
        session.add(review)
        await session.flush()
    return review


async def _review_snapshot(
    session: AsyncSession, user_id: UUID, meeting_id: UUID
) -> tuple[Meeting, list[dict[str, object]], list[WorkItem]]:
    meeting = await session.scalar(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
    )
    if meeting is None:
        raise MeetingReviewError("meeting not found")
    captures = list(
        await session.scalars(
            select(DraftSession)
            .options(selectinload(DraftSession.items))
            .where(
                DraftSession.user_id == user_id,
                DraftSession.meeting_id == meeting_id,
                DraftSession.capture_review_status != "removed",
            )
            .order_by(DraftSession.capture_sequence, DraftSession.id)
        )
    )
    notes = {
        value.id: value
        for value in await session.scalars(
            select(Note).where(
                Note.user_id == user_id,
                Note.id.in_([capture.source_note_id for capture in captures]),
            )
        )
    }
    payload: list[dict[str, object]] = [
        {
            "capture_id": str(capture.id),
            "sequence": capture.capture_sequence,
            "source": notes[capture.source_note_id].content,
            "context": capture.capture_context,
            "items": [
                {
                    "draft_item_id": str(item.id),
                    "payload": item.raw_payload,
                }
                for item in capture.items
            ],
        }
        for capture in captures
        if capture.source_note_id in notes
    ]
    person_ids = select(MeetingParticipant.person_id).where(
        MeetingParticipant.user_id == user_id,
        MeetingParticipant.meeting_id == meeting_id,
    )
    topic_ids = select(MeetingTopic.topic_id).where(
        MeetingTopic.user_id == user_id, MeetingTopic.meeting_id == meeting_id
    )
    agenda = list(
        await session.scalars(
            select(WorkItem)
            .where(
                WorkItem.user_id == user_id,
                WorkItem.status.in_(OPEN_STATUSES),
                WorkItem.type.in_(
                    (WorkItemType.AGENDA_ITEM.value, WorkItemType.QUESTION.value)
                ),
                or_(
                    WorkItem.topic_id.in_(topic_ids),
                    WorkItem.id.in_(
                        select(WorkItemPerson.work_item_id).where(
                            WorkItemPerson.user_id == user_id,
                            WorkItemPerson.person_id.in_(person_ids),
                        )
                    ),
                ),
            )
            .distinct()
            .order_by(WorkItem.updated_at, WorkItem.id)
        )
    )
    return meeting, payload, agenda


def build_review_prompt() -> str:
    return """Create a concise structured meeting review from the supplied captures.
Use only supplied capture_id, draft_item_id and agenda work_item_id values. Every
proposal must reference its source capture. Preserve unresolved uncertainty and
never claim to create, update, complete or send records. Split independent next
actions. Decisions may reference related proposal numbers. Return only the
requested schema; do not include technical logs or raw provider metadata."""


def _record_from_item(
    record: DraftItemRecord, item: DraftItem, assessment: DraftItemAssessment
) -> None:
    record.item_type = item.type.value
    record.title = item.title
    record.description = item.description
    record.people_candidates = item.person_candidates
    record.topic_candidates = item.topic_candidates
    temporal = item.due_date_candidate or item.reminder_candidate
    record.original_date_text = temporal.original_phrase if temporal else None
    record.normalized_date = temporal.normalized_value if temporal else None
    record.notes = item.notes
    record.missing_fields = item.missing_fields
    record.ambiguities = item.ambiguities
    record.confidence = item.confidence
    record.readiness = assessment.readiness.value
    record.raw_payload = assessment.model_dump(mode="json")


async def _store_review_result(
    session: AsyncSession,
    review: MeetingReview,
    result: MeetingReviewParseResult,
    *,
    high_threshold: float,
    clarification_threshold: float,
    now: datetime,
) -> MeetingReview:
    meeting = await session.scalar(
        select(Meeting)
        .where(Meeting.id == review.meeting_id, Meeting.user_id == review.user_id)
        .with_for_update()
    )
    if meeting is None or meeting.status != MeetingStatus.PROCESSING.value:
        raise MeetingReviewError("meeting review is stale")
    captures = {
        capture.id: capture
        for capture in await session.scalars(
            select(DraftSession)
            .options(selectinload(DraftSession.items))
            .where(
                DraftSession.user_id == review.user_id,
                DraftSession.meeting_id == review.meeting_id,
                DraftSession.capture_review_status != "removed",
            )
        )
    }
    agenda_ids = set(
        await session.scalars(
            select(MeetingAgendaEntry.work_item_id).where(
                MeetingAgendaEntry.user_id == review.user_id,
                MeetingAgendaEntry.meeting_id == review.meeting_id,
            )
        )
    )
    await session.execute(
        delete(MeetingReviewItem).where(MeetingReviewItem.review_id == review.id)
    )
    seen_draft_items: set[UUID] = set()
    for position, proposal in enumerate(result.proposals, start=1):
        capture = captures.get(proposal.source_capture_id)
        if capture is None:
            raise MeetingReviewError("review references an unknown capture")
        expected_type = CATEGORY_TYPES[proposal.category]
        if proposal.item.type not in {expected_type, DraftItemType.UNKNOWN}:
            raise MeetingReviewError("review category does not match item type")
        assessment = DraftItemAssessment(
            item=proposal.item,
            readiness=classify_readiness(
                proposal.item,
                result_ambiguities=[],
                high_threshold=high_threshold,
                clarification_threshold=clarification_threshold,
            ),
        )
        record = next(
            (
                value
                for value in capture.items
                if value.id == proposal.source_draft_item_id
            ),
            None,
        )
        if proposal.source_draft_item_id is not None and record is None:
            raise MeetingReviewError("review references an unknown draft item")
        if record is None:
            next_position = (
                max((value.position for value in capture.items), default=0) + 1
            )
            record = DraftItemRecord(
                draft_session_id=capture.id,
                position=next_position,
                item_type=proposal.item.type.value,
                title=proposal.item.title,
                confidence=proposal.item.confidence,
                readiness=assessment.readiness.value,
                raw_payload=assessment.model_dump(mode="json"),
            )
            session.add(record)
            await session.flush()
        if record.id in seen_draft_items:
            raise MeetingReviewError("draft item is proposed more than once")
        seen_draft_items.add(record.id)
        _record_from_item(record, proposal.item, assessment)
        status = (
            "ready"
            if assessment.readiness is DraftReadiness.READY
            and proposal.item.type is not DraftItemType.UNKNOWN
            else "clarification_required"
        )
        session.add(
            MeetingReviewItem(
                user_id=review.user_id,
                review_id=review.id,
                source_capture_id=capture.id,
                source_draft_item_id=record.id,
                position=position,
                origin="capture",
                category=proposal.category,
                status=status,
                title=proposal.item.title,
                raw_payload=assessment.model_dump(mode="json"),
                suggested_next_action=proposal.suggested_next_action,
                consequences=proposal.consequences,
                related_positions=proposal.related_proposal_numbers,
                clarification_question=proposal.clarification_question,
            )
        )
    for suggestion in result.agenda:
        if suggestion.work_item_id not in agenda_ids:
            raise MeetingReviewError("review references an unknown agenda item")
        entry = await session.scalar(
            select(MeetingAgendaEntry).where(
                MeetingAgendaEntry.meeting_id == review.meeting_id,
                MeetingAgendaEntry.work_item_id == suggestion.work_item_id,
            )
        )
        if entry is not None:
            entry.outcome = suggestion.outcome
            entry.result = suggestion.result
    review.summary = result.summary
    review.suggested_next_actions = result.suggested_next_actions
    review.status = "review_required"
    review.last_error_code = None
    review.updated_at = now
    meeting.summary = result.summary
    meeting.status = MeetingStatus.REVIEW_REQUIRED.value
    meeting.updated_at = now
    session.add(
        MeetingEvent(
            user_id=review.user_id,
            meeting_id=meeting.id,
            event_type=MeetingEventType.REVIEW_GENERATED.value,
            previous_status=MeetingStatus.PROCESSING.value,
            new_status=MeetingStatus.REVIEW_REQUIRED.value,
            payload={"item_count": len(result.proposals)},
        )
    )
    await session.flush()
    return review


async def generate_review(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    provider: MeetingReviewProvider | None,
    *,
    high_threshold: float,
    clarification_threshold: float,
    now: datetime | None = None,
) -> MeetingReview:
    timestamp = now or review_now()
    review = await ensure_review(session, user_id, meeting_id)
    if review.status in {"review_required", "completed"}:
        return review
    review.status = "processing"
    review.generation_attempts += 1
    review.updated_at = timestamp
    meeting, captures, agenda = await _review_snapshot(session, user_id, meeting_id)
    for item in agenda:
        existing = await session.scalar(
            select(MeetingAgendaEntry).where(
                MeetingAgendaEntry.meeting_id == meeting_id,
                MeetingAgendaEntry.work_item_id == item.id,
            )
        )
        if existing is None:
            session.add(
                MeetingAgendaEntry(
                    user_id=user_id,
                    meeting_id=meeting_id,
                    work_item_id=item.id,
                    outcome="pending",
                )
            )
    await session.flush()
    if not captures:
        empty = MeetingReviewParseResult(
            summary="Встреча завершена без сохранённых пунктов.",
            proposals=[],
            agenda=[],
            suggested_next_actions=[],
        )
        return await _store_review_result(
            session,
            review,
            empty,
            high_threshold=high_threshold,
            clarification_threshold=clarification_threshold,
            now=timestamp,
        )
    if provider is None:
        review.status = "failed"
        review.last_error_code = "provider_unavailable"
        session.add(
            MeetingEvent(
                user_id=user_id,
                meeting_id=meeting_id,
                event_type=MeetingEventType.REVIEW_FAILED.value,
                previous_status=MeetingStatus.PROCESSING.value,
                new_status=MeetingStatus.PROCESSING.value,
                payload={"category": "provider_unavailable"},
            )
        )
        await session.flush()
        raise MeetingReviewError("meeting review provider is unavailable")
    payload = {
        "meeting": {
            "id": str(meeting.id),
            "title": meeting.title,
            "type": meeting.type,
        },
        "captures": captures,
        "agenda": [
            {"work_item_id": str(item.id), "type": item.type, "title": item.title}
            for item in agenda
        ],
    }
    try:
        parsed = await provider.parse_meeting_review(
            system_prompt=build_review_prompt(),
            user_text=json.dumps(payload, ensure_ascii=False),
        )
        return await _store_review_result(
            session,
            review,
            parsed,
            high_threshold=high_threshold,
            clarification_threshold=clarification_threshold,
            now=timestamp,
        )
    except MeetingReviewError:
        raise
    except Exception as error:
        review.status = "failed"
        review.last_error_code = "processing_failed"
        session.add(
            MeetingEvent(
                user_id=user_id,
                meeting_id=meeting_id,
                event_type=MeetingEventType.REVIEW_FAILED.value,
                previous_status=MeetingStatus.PROCESSING.value,
                new_status=MeetingStatus.PROCESSING.value,
                payload={"category": "processing_failed"},
            )
        )
        await session.flush()
        raise MeetingReviewError("meeting review generation failed") from error


async def list_review_items(
    session: AsyncSession, user_id: UUID, review_id: UUID
) -> list[MeetingReviewItem]:
    return list(
        await session.scalars(
            select(MeetingReviewItem)
            .where(
                MeetingReviewItem.user_id == user_id,
                MeetingReviewItem.review_id == review_id,
            )
            .order_by(MeetingReviewItem.position, MeetingReviewItem.id)
        )
    )


async def serialize_review(
    session: AsyncSession, review: MeetingReview
) -> dict[str, object]:
    items = await list_review_items(session, review.user_id, review.id)
    agenda_rows = list(
        (
            await session.execute(
                select(MeetingAgendaEntry, WorkItem)
                .join(WorkItem, WorkItem.id == MeetingAgendaEntry.work_item_id)
                .where(
                    MeetingAgendaEntry.user_id == review.user_id,
                    MeetingAgendaEntry.meeting_id == review.meeting_id,
                    WorkItem.user_id == review.user_id,
                )
                .order_by(WorkItem.title, WorkItem.id)
            )
        ).all()
    )
    counts = Counter(item.category for item in items if item.status != "excluded")
    result_rows = list(
        (
            await session.execute(
                select(MeetingWorkItem, WorkItem)
                .join(WorkItem, WorkItem.id == MeetingWorkItem.work_item_id)
                .where(
                    MeetingWorkItem.user_id == review.user_id,
                    MeetingWorkItem.meeting_id == review.meeting_id,
                    WorkItem.user_id == review.user_id,
                )
                .order_by(MeetingWorkItem.created_at, MeetingWorkItem.id)
            )
        ).all()
    )
    events = list(
        await session.scalars(
            select(MeetingEvent)
            .where(
                MeetingEvent.user_id == review.user_id,
                MeetingEvent.meeting_id == review.meeting_id,
            )
            .order_by(MeetingEvent.created_at, MeetingEvent.id)
        )
    )
    return {
        "id": review.id,
        "meeting_id": review.meeting_id,
        "status": review.status,
        "summary": review.summary,
        "suggested_next_actions": review.suggested_next_actions,
        "counts": dict(counts),
        "revision": review_revision(review),
        "last_error_code": review.last_error_code,
        "items": [
            {
                "id": item.id,
                "position": item.position,
                "origin": item.origin,
                "category": item.category,
                "status": item.status,
                "title": item.title,
                "source_capture_id": item.source_capture_id,
                "source_draft_item_id": item.source_draft_item_id,
                "source_work_item_id": item.source_work_item_id,
                "payload": item.raw_payload,
                "suggested_next_action": item.suggested_next_action,
                "consequences": item.consequences,
                "clarification_question": item.clarification_question,
                "planner_requested": item.planner_requested,
                "result_work_item_id": item.result_work_item_id,
                "result_note_id": item.result_note_id,
            }
            for item in items
        ],
        "agenda": [
            {
                "id": entry.id,
                "work_item_id": work_item.id,
                "title": work_item.title,
                "type": work_item.type,
                "outcome": entry.outcome,
                "result": entry.result,
            }
            for entry, work_item in agenda_rows
        ],
        "results": [
            {
                "id": work_item.id,
                "type": work_item.type,
                "title": work_item.title,
                "status": work_item.status,
                "due_at": work_item.due_at,
                "next_follow_up_at": work_item.next_follow_up_at,
                "planner_status": work_item.planner_status,
                "role": link.role,
            }
            for link, work_item in result_rows
        ],
        "timeline": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "previous_status": event.previous_status,
                "new_status": event.new_status,
                "created_at": event.created_at,
            }
            for event in events
        ],
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }


async def set_review_item_action(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    item_id: UUID,
    *,
    action: str,
    expected_revision: int,
) -> MeetingReview:
    review = await get_review(session, user_id, meeting_id, for_update=True)
    if review is None or review.status != "review_required":
        raise MeetingReviewError("review is not editable")
    if review_revision(review) != expected_revision:
        raise MeetingReviewError("review is stale")
    item = await session.scalar(
        select(MeetingReviewItem).where(
            MeetingReviewItem.id == item_id,
            MeetingReviewItem.user_id == user_id,
            MeetingReviewItem.review_id == review.id,
        )
    )
    if item is None:
        raise MeetingReviewError("review item not found")
    if action == "exclude":
        item.status = "excluded"
    elif action == "include":
        assessment = DraftItemAssessment.model_validate(item.raw_payload)
        item.status = (
            "ready"
            if assessment.readiness is DraftReadiness.READY
            else "clarification_required"
        )
    elif action == "planner_on":
        if item.category not in {"task", "follow_up", "waiting"}:
            raise MeetingReviewError("item is not eligible for Planner")
        item.planner_requested = True
    elif action == "planner_off":
        item.planner_requested = False
    elif action == "inbox":
        item.status = "inbox"
    else:
        raise MeetingReviewError("unsupported review action")
    review.updated_at = review_now()
    await session.flush()
    return review


async def answer_review_item(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    item_id: UUID,
    answer: str,
    *,
    expected_revision: int | None = None,
    telegram_update_id: int | None = None,
) -> MeetingReview:
    review = await get_review(session, user_id, meeting_id, for_update=True)
    if review is None or review.status != "review_required":
        raise MeetingReviewError("review is not editable")
    if expected_revision is not None and review_revision(review) != expected_revision:
        raise MeetingReviewError("review is stale")
    if (
        telegram_update_id is not None
        and telegram_update_id in review.processed_update_ids
    ):
        return review
    item = await session.scalar(
        select(MeetingReviewItem).where(
            MeetingReviewItem.id == item_id,
            MeetingReviewItem.user_id == user_id,
            MeetingReviewItem.review_id == review.id,
        )
    )
    normalized = answer.strip()
    if item is None or item.status != "clarification_required":
        raise MeetingReviewError("review item is not awaiting clarification")
    if not normalized:
        raise MeetingReviewError("clarification answer is empty")
    assessment = DraftItemAssessment.model_validate(item.raw_payload)
    if assessment.item.type is DraftItemType.UNKNOWN:
        raise MeetingReviewError("item type must be selected in the meeting review")
    item.clarification_answer = normalized
    item.status = "ready"
    review.current_item_id = None
    review.current_question = None
    review.current_question_context = None
    review.current_question_message_id = None
    if telegram_update_id is not None:
        review.processed_update_ids = [*review.processed_update_ids, telegram_update_id]
    review.updated_at = review_now()
    session.add(
        MeetingEvent(
            user_id=user_id,
            meeting_id=meeting_id,
            event_type=MeetingEventType.CLARIFICATION_ANSWERED.value,
            previous_status=MeetingStatus.REVIEW_REQUIRED.value,
            new_status=MeetingStatus.REVIEW_REQUIRED.value,
            telegram_update_id=telegram_update_id,
            payload={"review_item_id": str(item.id)},
        )
    )
    await session.flush()
    return review


async def add_review_agenda_item(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    title: str,
) -> MeetingAgendaEntry:
    review = await get_review(session, user_id, meeting_id, for_update=True)
    if review is None or review.status != "review_required":
        raise MeetingReviewError("review is not editable")
    meeting = await session.scalar(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
    )
    if meeting is None:
        raise MeetingReviewError("meeting not found")
    work_item = await create_work_item(
        session,
        user_id,
        item_type=WorkItemType.AGENDA_ITEM,
        title=title,
        status=WorkItemStatus.INBOX,
        topic_id=meeting.primary_topic_id,
    )
    entry = MeetingAgendaEntry(
        user_id=user_id,
        meeting_id=meeting_id,
        work_item_id=work_item.id,
        outcome="pending",
    )
    session.add(entry)
    session.add(
        MeetingWorkItem(
            user_id=user_id,
            meeting_id=meeting_id,
            work_item_id=work_item.id,
            role="agenda",
        )
    )
    review.updated_at = review_now()
    session.add(
        MeetingEvent(
            user_id=user_id,
            meeting_id=meeting_id,
            event_type=MeetingEventType.AGENDA_UPDATED.value,
            previous_status=MeetingStatus.REVIEW_REQUIRED.value,
            new_status=MeetingStatus.REVIEW_REQUIRED.value,
            payload={"work_item_id": str(work_item.id), "action": "added"},
        )
    )
    await session.flush()
    return entry


async def set_agenda_outcome(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    entry_id: UUID,
    outcome: str,
    result: str | None,
) -> MeetingAgendaEntry:
    if outcome not in {"discussed", "answered", "deferred", "unresolved"}:
        raise MeetingReviewError("invalid agenda outcome")
    entry = await session.scalar(
        select(MeetingAgendaEntry).where(
            MeetingAgendaEntry.id == entry_id,
            MeetingAgendaEntry.user_id == user_id,
            MeetingAgendaEntry.meeting_id == meeting_id,
        )
    )
    if entry is None:
        raise MeetingReviewError("agenda entry not found")
    if outcome == "answered" and not (result or "").strip():
        raise MeetingReviewError("answered question requires a result")
    entry.outcome = outcome
    entry.result = (result or "").strip() or None
    review = await get_review(session, user_id, meeting_id, for_update=True)
    if review is not None:
        review.updated_at = review_now()
    session.add(
        MeetingEvent(
            user_id=user_id,
            meeting_id=meeting_id,
            event_type=MeetingEventType.AGENDA_UPDATED.value,
            previous_status=MeetingStatus.REVIEW_REQUIRED.value,
            new_status=MeetingStatus.REVIEW_REQUIRED.value,
            payload={"work_item_id": str(entry.work_item_id), "outcome": outcome},
        )
    )
    await session.flush()
    return entry


async def confirm_review(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    *,
    expected_revision: int,
    client_action_id: UUID | None = None,
    telegram_update_id: int | None = None,
    move_incomplete_to_inbox: bool,
    conversion_service: DraftConversionService,
    now: datetime | None = None,
) -> ReviewConfirmation:
    timestamp = now or review_now()
    review = await get_review(session, user_id, meeting_id, for_update=True)
    meeting = await session.scalar(
        select(Meeting)
        .where(Meeting.id == meeting_id, Meeting.user_id == user_id)
        .with_for_update()
    )
    if review is None or meeting is None:
        raise MeetingReviewError("review not found")
    if (client_action_id is None) == (telegram_update_id is None):
        raise MeetingReviewError("exactly one conversion origin is required")
    origin_filter = (
        MeetingEvent.client_action_id == client_action_id
        if client_action_id is not None
        else MeetingEvent.telegram_update_id == telegram_update_id
    )
    duplicate = await session.scalar(
        select(MeetingEvent).where(
            MeetingEvent.user_id == user_id,
            origin_filter,
            MeetingEvent.event_type == MeetingEventType.CONVERTED.value,
        )
    )
    if duplicate is not None:
        ids = tuple(UUID(value) for value in duplicate.payload.get("work_item_ids", []))
        notes = tuple(UUID(value) for value in duplicate.payload.get("note_ids", []))
        return ReviewConfirmation(
            ids,
            notes,
            int(duplicate.payload.get("inbox_count", 0)),
            meeting.status == MeetingStatus.COMPLETED.value,
        )
    if review.status == "completed":
        linked = tuple(
            await session.scalars(
                select(MeetingWorkItem.work_item_id).where(
                    MeetingWorkItem.user_id == user_id,
                    MeetingWorkItem.meeting_id == meeting_id,
                )
            )
        )
        return ReviewConfirmation(linked, (), 0, True)
    if (
        review.status != "review_required"
        or review_revision(review) != expected_revision
    ):
        raise MeetingReviewError("review is stale")
    items = await list_review_items(session, user_id, review.id)
    incomplete = [
        item for item in items if item.status in {"pending", "clarification_required"}
    ]
    if move_incomplete_to_inbox:
        for item in incomplete:
            item.status = "inbox"
    ready = [item for item in items if item.status == "ready"]
    converted_ids: list[UUID] = []
    note_ids: list[UUID] = []
    by_capture: dict[UUID, list[MeetingReviewItem]] = {}
    for item in ready:
        if item.source_capture_id is not None and item.source_draft_item_id is not None:
            by_capture.setdefault(item.source_capture_id, []).append(item)
    result_by_position: dict[int, UUID] = {}
    for capture_id, selected in by_capture.items():
        status_overrides = {
            item.source_draft_item_id: WorkItemStatus.DONE
            for item in selected
            if item.source_draft_item_id is not None
            and item.category in {"decision", "answered_question"}
        }
        planner_overrides = {
            item.source_draft_item_id: (
                PlannerStatus.NEEDS_TRANSFER
                if item.planner_requested
                else PlannerStatus.NOT_REQUIRED
            )
            for item in selected
            if item.source_draft_item_id is not None
            and item.category in {"task", "follow_up", "waiting"}
        }
        result = await conversion_service.convert(
            session,
            draft_id=capture_id,
            user_id=user_id,
            allow_incomplete=True,
            selected_item_ids=cast(
                set[UUID], {item.source_draft_item_id for item in selected}
            ),
            status_overrides=status_overrides,
            planner_overrides=planner_overrides,
        )
        by_source = {value.source_draft_item_id: value for value in result.work_items}
        notes_by_source = {value.source_draft_item_id: value for value in result.notes}
        for item in selected:
            work_item = by_source.get(item.source_draft_item_id)
            note = notes_by_source.get(item.source_draft_item_id)
            if work_item is not None:
                item.result_work_item_id = work_item.id
                role = "decision" if item.category == "decision" else "result"
                session.add(
                    MeetingWorkItem(
                        user_id=user_id,
                        meeting_id=meeting_id,
                        work_item_id=work_item.id,
                        review_item_id=item.id,
                        role=role,
                    )
                )
                converted_ids.append(work_item.id)
                result_by_position[item.position] = work_item.id
            if note is not None:
                item.result_note_id = note.id
                note_ids.append(note.id)
            item.status = "converted"
    for item in ready:
        if item.result_work_item_id is None:
            continue
        for target_position in item.related_positions:
            target_id = result_by_position.get(target_position)
            if target_id is not None and target_id != item.result_work_item_id:
                await create_work_item_relation(
                    session,
                    user_id,
                    item.result_work_item_id,
                    target_id,
                    WorkItemRelationType.RELATED_TO,
                )
    agenda = list(
        await session.scalars(
            select(MeetingAgendaEntry).where(
                MeetingAgendaEntry.user_id == user_id,
                MeetingAgendaEntry.meeting_id == meeting_id,
            )
        )
    )
    for entry in agenda:
        if entry.outcome in {"discussed", "answered"}:
            work_item = await session.scalar(
                select(WorkItem).where(
                    WorkItem.id == entry.work_item_id, WorkItem.user_id == user_id
                )
            )
            if work_item is not None and work_item.status in OPEN_STATUSES:
                previous_origin = session.info.get("client_action_id")
                agenda_origin = uuid5(
                    NAMESPACE_URL,
                    "meeting-review:"
                    f"{meeting_id}:{client_action_id or telegram_update_id}:"
                    f"{entry.work_item_id}",
                )
                bind_client_action(session, agenda_origin)
                try:
                    await complete_work_item(session, user_id, work_item.id, None)
                finally:
                    if isinstance(previous_origin, UUID):
                        bind_client_action(session, previous_origin)
                    else:
                        session.info.pop("client_action_id", None)
        existing_link = await session.scalar(
            select(MeetingWorkItem).where(
                MeetingWorkItem.meeting_id == meeting_id,
                MeetingWorkItem.work_item_id == entry.work_item_id,
            )
        )
        if existing_link is None:
            session.add(
                MeetingWorkItem(
                    user_id=user_id,
                    meeting_id=meeting_id,
                    work_item_id=entry.work_item_id,
                    role="agenda",
                )
            )
    remaining = [
        item
        for item in items
        if item.status in {"pending", "clarification_required", "ready"}
    ]
    completed = not remaining
    if completed:
        review.status = "completed"
        review.confirmed_at = timestamp
        meeting.status = MeetingStatus.COMPLETED.value
    review.updated_at = timestamp
    meeting.updated_at = timestamp
    session.add(
        MeetingEvent(
            user_id=user_id,
            meeting_id=meeting_id,
            event_type=MeetingEventType.CONVERTED.value,
            previous_status=MeetingStatus.REVIEW_REQUIRED.value,
            new_status=meeting.status,
            client_action_id=client_action_id,
            telegram_update_id=telegram_update_id,
            payload={
                "work_item_ids": [str(value) for value in converted_ids],
                "note_ids": [str(value) for value in note_ids],
                "inbox_count": sum(item.status == "inbox" for item in items),
            },
        )
    )
    if completed:
        session.add(
            MeetingEvent(
                user_id=user_id,
                meeting_id=meeting_id,
                event_type=MeetingEventType.COMPLETED.value,
                previous_status=MeetingStatus.REVIEW_REQUIRED.value,
                new_status=MeetingStatus.COMPLETED.value,
                payload={},
            )
        )
    await session.flush()
    return ReviewConfirmation(
        tuple(converted_ids),
        tuple(note_ids),
        sum(item.status == "inbox" for item in items),
        completed,
    )
