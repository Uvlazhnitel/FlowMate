import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from flowmate.ai.provider import MeetingReviewProvider
from flowmate.ai.schemas import DraftSource, MeetingDraftContext
from flowmate.ai.service import DraftParsingService
from flowmate.db.drafts import load_analysis, replace_draft_analysis
from flowmate.db.models import AIProcessingJob, DraftSession, Note
from flowmate.db.session import session_scope
from flowmate.drafts.questions import next_clarification_question
from flowmate.meetings.review import generate_review
from flowmate.stabilization.audit import record_audit_event
from flowmate.stabilization.jobs import (
    ClaimedAIJob,
    claim_due_ai_jobs,
    complete_ai_job,
    fail_ai_job,
)

logger = logging.getLogger(__name__)


class AIRecoveryProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        parsing_service: DraftParsingService | None,
        meeting_review_provider: MeetingReviewProvider | None,
        *,
        draft_ttl_hours: int,
        high_threshold: float,
        clarification_threshold: float,
        max_attempts: int = 3,
        lease_seconds: int = 300,
        batch_size: int = 10,
    ) -> None:
        self._session_factory = session_factory
        self._parsing_service = parsing_service
        self._meeting_review_provider = meeting_review_provider
        self._draft_ttl_hours = draft_ttl_hours
        self._high_threshold = high_threshold
        self._clarification_threshold = clarification_threshold
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds
        self._batch_size = batch_size

    async def process_due_jobs(self) -> None:
        if self._parsing_service is None and self._meeting_review_provider is None:
            return
        async with session_scope(self._session_factory) as session:
            claims = await claim_due_ai_jobs(
                session,
                limit=self._batch_size,
                max_attempts=self._max_attempts,
                lease_seconds=self._lease_seconds,
            )
        for claim in claims:
            try:
                await self._process_claim(claim)
            except Exception as error:
                async with session_scope(self._session_factory) as session:
                    await fail_ai_job(
                        session,
                        claim,
                        error_code=type(error).__name__.lower()[:64],
                        max_attempts=self._max_attempts,
                        retry_delay=timedelta(minutes=1),
                    )
                logger.warning(
                    "ai_recovery_failed job_id=%s category=%s",
                    claim.id,
                    type(error).__name__,
                )

    async def _process_claim(self, claim: ClaimedAIJob) -> None:
        async with session_scope(self._session_factory) as session:
            job = await session.scalar(
                select(AIProcessingJob).where(
                    AIProcessingJob.id == claim.id,
                    AIProcessingJob.status == "processing",
                    AIProcessingJob.lease_token == claim.lease_token,
                )
            )
            if job is None:
                return
            if job.job_kind in {"draft_parse", "meeting_capture_parse"}:
                await self._recover_draft(session, job)
            elif job.job_kind == "draft_refine":
                await self._recover_refinement(session, job)
            elif job.job_kind == "meeting_review_generate":
                await generate_review(
                    session,
                    job.user_id,
                    job.entity_id,
                    self._meeting_review_provider,
                    high_threshold=self._high_threshold,
                    clarification_threshold=self._clarification_threshold,
                )
            else:
                raise ValueError("unsupported recovery job")
            await complete_ai_job(session, claim)
            await record_audit_event(
                session,
                actor_kind="system",
                action="ai.recovered",
                outcome="recovered",
                user_id=job.user_id,
                entity_kind="ai_job",
                entity_id=job.id,
                safe_metadata={
                    "job_kind": job.job_kind,
                    "attempt_count": job.attempt_count,
                    "prompt_version": job.prompt_version,
                },
            )

    async def _recover_draft(self, session: AsyncSession, job: AIProcessingJob) -> None:
        if self._parsing_service is None:
            raise RuntimeError("AI parser is unavailable")
        draft = await session.scalar(
            select(DraftSession)
            .options(selectinload(DraftSession.items))
            .where(
                DraftSession.id == job.entity_id, DraftSession.user_id == job.user_id
            )
            .with_for_update()
        )
        if draft is None:
            raise ValueError("draft not found")
        if draft.status not in {"parsing", "failed"}:
            return
        note = await session.scalar(
            select(Note).where(
                Note.id == draft.source_note_id, Note.user_id == draft.user_id
            )
        )
        if note is None or note.content is None:
            raise ValueError("draft source is unavailable")
        source = DraftSource(note.source)
        context = None
        if draft.meeting_id is not None:
            raw = draft.capture_context
            timezone = ZoneInfo(str(raw["timezone"]))
            topics = list(raw.get("topics", []))
            primary_id = raw.get("primary_topic_id")
            meeting_context = MeetingDraftContext.model_validate(
                {
                    "meeting_id": raw["meeting_id"],
                    "meeting_type": raw["meeting_type"],
                    "participants": [
                        value["name"] for value in raw.get("participants", [])
                    ],
                    "topics": [value["name"] for value in topics],
                    "primary_topic": next(
                        (
                            value["name"]
                            for value in topics
                            if value.get("id") == primary_id
                        ),
                        None,
                    ),
                }
            )
            context = self._parsing_service.build_meeting_context(
                source=source,
                timezone=timezone,
                current_datetime=draft.created_at,
                meeting=meeting_context,
            )
        analysis = await self._parsing_service.parse(
            note.content, source=source, context=context
        )
        await replace_draft_analysis(
            session,
            draft,
            analysis,
            question=next_clarification_question(analysis),
            ttl_hours=self._draft_ttl_hours,
        )

    async def _recover_refinement(
        self, session: AsyncSession, job: AIProcessingJob
    ) -> None:
        if self._parsing_service is None:
            raise RuntimeError("AI parser is unavailable")
        draft = await session.scalar(
            select(DraftSession)
            .options(selectinload(DraftSession.items))
            .where(
                DraftSession.id == job.entity_id,
                DraftSession.user_id == job.user_id,
                DraftSession.meeting_id.is_(None),
            )
            .with_for_update()
        )
        if draft is None:
            raise ValueError("draft not found")
        if draft.status not in {"needs_clarification", "ready"}:
            return
        if job.input_text is None or job.input_source is None:
            raise ValueError("refinement input is unavailable")
        current = load_analysis(draft)
        question_text = draft.current_question or "Уточните данные пункта."
        analysis = await self._parsing_service.refine(
            current,
            job.input_text,
            answer_source=DraftSource(job.input_source),
            question=question_text,
        )
        await replace_draft_analysis(
            session,
            draft,
            analysis,
            question=next_clarification_question(analysis),
            ttl_hours=self._draft_ttl_hours,
        )


class MaintenanceProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        terminal_transcript_days: int,
        unresolved_transcript_days: int,
        expired_record_days: int,
    ) -> None:
        self._session_factory = session_factory
        self._terminal_transcript_days = terminal_transcript_days
        self._unresolved_transcript_days = unresolved_transcript_days
        self._expired_record_days = expired_record_days

    async def run_cleanup(self) -> None:
        from flowmate.stabilization.cleanup import run_database_cleanup

        async with session_scope(self._session_factory) as session:
            await run_database_cleanup(
                session,
                terminal_transcript_days=self._terminal_transcript_days,
                unresolved_transcript_days=self._unresolved_transcript_days,
                expired_record_days=self._expired_record_days,
            )
