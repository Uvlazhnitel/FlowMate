from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import (
    DraftItemRecord,
    DraftSession,
    Meeting,
    MeetingNote,
    MeetingSetupSession,
    Note,
    PwaLoginCode,
    PwaSession,
    WorkItem,
    WorkItemActionSession,
)
from flowmate.stabilization.audit import record_audit_event


@dataclass(frozen=True, slots=True)
class CleanupResult:
    expired_drafts: int
    redacted_transcripts: int
    purged_draft_payloads: int
    deleted_auth_records: int
    deleted_action_sessions: int


def cleanup_now() -> datetime:
    return datetime.now(UTC)


def affected_rows(result: Result[Any]) -> int:
    return int(getattr(result, "rowcount", 0))


async def run_database_cleanup(
    session: AsyncSession,
    *,
    terminal_transcript_days: int = 30,
    unresolved_transcript_days: int = 90,
    expired_record_days: int = 30,
    now: datetime | None = None,
) -> CleanupResult:
    if (
        min(
            terminal_transcript_days,
            unresolved_transcript_days,
            expired_record_days,
        )
        <= 0
    ):
        raise ValueError("cleanup retention periods must be positive")
    current = now or cleanup_now()
    terminal_cutoff = current - timedelta(days=terminal_transcript_days)
    unresolved_cutoff = current - timedelta(days=unresolved_transcript_days)
    expired_cutoff = current - timedelta(days=expired_record_days)

    expired = await session.execute(
        update(DraftSession)
        .where(
            DraftSession.meeting_id.is_(None),
            DraftSession.status.in_(("parsing", "needs_clarification", "ready")),
            DraftSession.expires_at <= current,
        )
        .values(
            status="expired",
            processing_update_id=None,
            processing_started_at=None,
            current_question=None,
            current_question_options=[],
            current_question_context=None,
            current_question_message_id=None,
        )
    )

    terminal_note = or_(
        exists(
            select(WorkItem.id).where(
                WorkItem.source_note_id == Note.id,
                WorkItem.user_id == Note.user_id,
            )
        ),
        exists(
            select(MeetingNote.id)
            .join(Meeting, Meeting.id == MeetingNote.meeting_id)
            .where(
                MeetingNote.note_id == Note.id,
                MeetingNote.user_id == Note.user_id,
                Meeting.status == "completed",
            )
        ),
    )
    redacted = await session.execute(
        update(Note)
        .where(
            Note.source == "voice",
            Note.content.is_not(None),
            or_(
                Note.created_at <= unresolved_cutoff,
                (Note.created_at <= terminal_cutoff) & terminal_note,
            ),
        )
        .values(content=None, transcript_redacted_at=current)
    )

    old_draft_ids = select(DraftSession.id).where(
        DraftSession.meeting_id.is_(None),
        DraftSession.status.in_(("expired", "cancelled", "failed")),
        DraftSession.updated_at <= expired_cutoff,
    )
    purged_items = await session.execute(
        update(DraftItemRecord)
        .where(DraftItemRecord.draft_session_id.in_(old_draft_ids))
        .values(
            raw_payload={"purged": True},
            notes=[],
            missing_fields=[],
            ambiguities=[],
        )
    )
    await session.execute(
        update(DraftSession)
        .where(DraftSession.id.in_(old_draft_ids))
        .values(
            analysis_payload=None,
            current_question=None,
            current_question_options=[],
            current_question_context=None,
            current_question_message_id=None,
            processed_update_ids=[],
            processing_update_id=None,
            processing_started_at=None,
        )
    )

    login_codes = await session.execute(
        delete(PwaLoginCode).where(PwaLoginCode.expires_at <= expired_cutoff)
    )
    pwa_sessions = await session.execute(
        delete(PwaSession).where(
            or_(
                PwaSession.expires_at <= expired_cutoff,
                PwaSession.revoked_at <= expired_cutoff,
            )
        )
    )
    action_sessions = await session.execute(
        delete(WorkItemActionSession).where(
            WorkItemActionSession.expires_at <= expired_cutoff
        )
    )
    setup_sessions = await session.execute(
        delete(MeetingSetupSession).where(
            MeetingSetupSession.expires_at <= expired_cutoff
        )
    )
    result = CleanupResult(
        expired_drafts=affected_rows(expired),
        redacted_transcripts=affected_rows(redacted),
        purged_draft_payloads=affected_rows(purged_items),
        deleted_auth_records=affected_rows(login_codes) + affected_rows(pwa_sessions),
        deleted_action_sessions=affected_rows(action_sessions)
        + affected_rows(setup_sessions),
    )
    await record_audit_event(
        session,
        actor_kind="system",
        action="maintenance.cleanup",
        outcome="success",
        entity_kind="maintenance",
        safe_metadata={
            "count": (
                result.expired_drafts
                + result.redacted_transcripts
                + result.purged_draft_payloads
                + result.deleted_auth_records
                + result.deleted_action_sessions
            )
        },
    )
    return result
