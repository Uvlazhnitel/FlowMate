import json
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowmate.ai.prompt_versions import DRAFT_PROMPT_VERSION
from flowmate.ai.schemas import (
    DraftAnalysisResult,
    DraftItemAssessment,
    DraftReadiness,
)
from flowmate.db.models import DraftItemRecord, DraftSession
from flowmate.db.models.draft import OPEN_DRAFT_STATUSES
from flowmate.drafts.questions import ClarificationQuestion
from flowmate.stabilization.jobs import complete_entity_ai_jobs, enqueue_ai_job

DraftStatus = Literal[
    "parsing",
    "needs_clarification",
    "ready",
    "confirmed",
    "cancelled",
    "expired",
    "failed",
]
ClaimResult = Literal["claimed", "duplicate", "busy", "inactive"]
PROCESSING_LEASE = timedelta(minutes=5)


def utc_now() -> datetime:
    return datetime.now(UTC)


def expires_after(now: datetime, ttl_hours: int) -> datetime:
    return now + timedelta(hours=ttl_hours)


async def create_parsing_draft(
    session: AsyncSession,
    *,
    user_id: UUID,
    source_note_id: UUID,
    ttl_hours: int,
    now: datetime | None = None,
) -> DraftSession:
    current = now or utc_now()
    draft = DraftSession(
        user_id=user_id,
        source_note_id=source_note_id,
        status="parsing",
        prompt_version=DRAFT_PROMPT_VERSION,
        expires_at=expires_after(current, ttl_hours),
    )
    session.add(draft)
    await session.flush()
    await enqueue_ai_job(
        session,
        user_id=user_id,
        job_kind="draft_parse",
        entity_id=draft.id,
        operation_key="initial",
        prompt_name="draft",
        prompt_version=DRAFT_PROMPT_VERSION,
        input_source="note",
        now=current,
    )
    return draft


async def get_draft_by_source_note(
    session: AsyncSession,
    source_note_id: UUID,
) -> DraftSession | None:
    statement = (
        select(DraftSession)
        .options(selectinload(DraftSession.items))
        .where(DraftSession.source_note_id == source_note_id)
    )
    return (await session.scalars(statement)).one_or_none()


async def get_draft_for_user(
    session: AsyncSession,
    draft_id: UUID,
    user_id: UUID,
    *,
    for_update: bool = False,
) -> DraftSession | None:
    statement = (
        select(DraftSession)
        .options(selectinload(DraftSession.items))
        .where(
            DraftSession.id == draft_id,
            DraftSession.user_id == user_id,
            DraftSession.meeting_id.is_(None),
        )
    )
    if for_update:
        statement = statement.with_for_update()
    return (await session.scalars(statement)).one_or_none()


async def get_active_draft_for_user(
    session: AsyncSession,
    user_id: UUID,
    *,
    now: datetime | None = None,
) -> DraftSession | None:
    statement = (
        select(DraftSession)
        .options(selectinload(DraftSession.items))
        .where(
            DraftSession.user_id == user_id,
            DraftSession.meeting_id.is_(None),
            DraftSession.status.in_(OPEN_DRAFT_STATUSES),
        )
        .order_by(DraftSession.created_at.desc())
    )
    draft = (await session.scalars(statement)).first()
    if draft is not None and draft.expires_at <= (now or utc_now()):
        draft.status = "expired"
        draft.processing_update_id = None
        draft.processing_started_at = None
        await session.flush()
        return None
    return draft


async def get_draft_by_question_message(
    session: AsyncSession,
    *,
    user_id: UUID,
    message_id: int,
) -> DraftSession | None:
    statement = (
        select(DraftSession)
        .options(selectinload(DraftSession.items))
        .where(
            DraftSession.user_id == user_id,
            DraftSession.meeting_id.is_(None),
            DraftSession.current_question_message_id == message_id,
        )
        .order_by(DraftSession.created_at.desc())
    )
    return (await session.scalars(statement)).first()


async def get_draft_by_processed_update(
    session: AsyncSession,
    *,
    user_id: UUID,
    update_id: int,
) -> DraftSession | None:
    statement = (
        select(DraftSession)
        .where(
            DraftSession.user_id == user_id,
            DraftSession.meeting_id.is_(None),
            DraftSession.processed_update_ids.contains([update_id]),
        )
        .order_by(DraftSession.created_at.desc())
    )
    return (await session.scalars(statement)).first()


def primary_temporal(
    assessment: DraftItemAssessment,
) -> tuple[str | None, datetime | None]:
    item = assessment.item
    candidate = item.due_date_candidate or item.reminder_candidate
    if candidate is None:
        return None, None
    return candidate.original_phrase, candidate.normalized_value


def analysis_status(analysis: DraftAnalysisResult) -> DraftStatus:
    if all(
        assessment.readiness is DraftReadiness.READY for assessment in analysis.items
    ):
        return "ready"
    return "needs_clarification"


async def replace_draft_analysis(
    session: AsyncSession,
    draft: DraftSession,
    analysis: DraftAnalysisResult,
    *,
    question: ClarificationQuestion | None,
    ttl_hours: int,
    now: datetime | None = None,
) -> None:
    current = now or utc_now()
    draft.analysis_payload = analysis.model_dump(mode="json")
    draft.status = analysis_status(analysis)
    draft.current_question = question.text if question is not None else None
    draft.current_question_options = (
        [option.to_dict() for option in question.options]
        if question is not None
        else []
    )
    draft.current_question_context = question.context if question is not None else None
    draft.current_question_message_id = None
    draft.processing_update_id = None
    draft.processing_started_at = None
    draft.expires_at = expires_after(current, ttl_hours)
    await session.execute(
        delete(DraftItemRecord).where(DraftItemRecord.draft_session_id == draft.id)
    )
    for position, assessment in enumerate(analysis.items, start=1):
        original_date_text, normalized_date = primary_temporal(assessment)
        item = assessment.item
        session.add(
            DraftItemRecord(
                draft_session_id=draft.id,
                position=position,
                item_type=item.type.value,
                title=item.title,
                description=item.description,
                people_candidates=item.person_candidates,
                topic_candidates=item.topic_candidates,
                original_date_text=original_date_text,
                normalized_date=normalized_date,
                notes=item.notes,
                missing_fields=item.missing_fields,
                ambiguities=item.ambiguities,
                confidence=item.confidence,
                readiness=assessment.readiness.value,
                raw_payload=assessment.model_dump(mode="json"),
            )
        )
    await session.flush()
    await session.refresh(draft, attribute_names=["items"])
    await complete_entity_ai_jobs(
        session,
        entity_id=draft.id,
        job_kinds=("draft_parse", "meeting_capture_parse", "draft_refine"),
        now=current,
    )


def load_analysis(draft: DraftSession) -> DraftAnalysisResult:
    if draft.analysis_payload is None:
        raise ValueError("draft does not contain an analysis payload")
    return DraftAnalysisResult.model_validate_json(json.dumps(draft.analysis_payload))


async def transition_draft(
    session: AsyncSession,
    draft: DraftSession,
    status: DraftStatus,
) -> None:
    draft.status = status
    draft.current_question = None
    draft.current_question_options = []
    draft.current_question_context = None
    draft.current_question_message_id = None
    draft.processing_update_id = None
    draft.processing_started_at = None
    await session.flush()


async def set_question_message_id(
    session: AsyncSession,
    draft: DraftSession,
    message_id: int,
) -> None:
    draft.current_question_message_id = message_id
    await session.flush()


async def claim_update(
    session: AsyncSession,
    *,
    draft_id: UUID,
    user_id: UUID,
    update_id: int,
    ttl_hours: int,
    now: datetime | None = None,
) -> tuple[ClaimResult, DraftSession | None]:
    draft = await get_draft_for_user(
        session,
        draft_id,
        user_id,
        for_update=True,
    )
    current = now or utc_now()
    if draft is None or draft.status not in OPEN_DRAFT_STATUSES:
        return "inactive", draft
    if draft.expires_at <= current:
        await transition_draft(session, draft, "expired")
        return "inactive", draft
    if update_id in draft.processed_update_ids:
        return "duplicate", draft
    if draft.processing_update_id is not None:
        started_at = draft.processing_started_at
        if started_at is None or started_at > current - PROCESSING_LEASE:
            return "busy", draft
    draft.processed_update_ids = [*draft.processed_update_ids[-99:], update_id]
    draft.processing_update_id = update_id
    draft.processing_started_at = current
    draft.expires_at = expires_after(current, ttl_hours)
    await session.flush()
    return "claimed", draft


async def clear_processing_update(
    session: AsyncSession,
    draft: DraftSession,
) -> None:
    draft.processing_update_id = None
    draft.processing_started_at = None
    await session.flush()
