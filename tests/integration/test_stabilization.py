from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import AuditEvent, Note
from flowmate.db.users import create_telegram_user
from flowmate.reminders.enums import ReminderStatus
from flowmate.reminders.service import (
    claim_due_reminders,
    create_reminder,
    mark_reminder_delivery_started,
)
from flowmate.stabilization.audit import record_audit_event
from flowmate.stabilization.cleanup import run_database_cleanup
from flowmate.stabilization.idempotency import (
    claim_telegram_update,
    complete_telegram_update,
)
from flowmate.stabilization.jobs import (
    claim_due_ai_jobs,
    complete_ai_job,
    enqueue_ai_job,
)
from flowmate.task_engine.service import create_work_item


@pytest.mark.integration
async def test_persistent_telegram_receipt_survives_repeated_claim(
    database_session: AsyncSession,
) -> None:
    first = await claim_telegram_update(
        database_session,
        update_id=9_100_001,
        telegram_user_id=1001,
        event_kind="message",
    )
    await complete_telegram_update(database_session, 9_100_001)
    duplicate = await claim_telegram_update(
        database_session,
        update_id=9_100_001,
        telegram_user_id=1001,
        event_kind="message",
    )

    assert first.accepted is True
    assert duplicate.accepted is False
    assert duplicate.duplicate is True


@pytest.mark.integration
async def test_audit_events_are_append_only(database_session: AsyncSession) -> None:
    user = await create_telegram_user(database_session, 9_100_005)
    event = await record_audit_event(
        database_session,
        actor_kind="system",
        action="test.safe_event",
        outcome="success",
        user_id=user.id,
        entity_kind="user",
        entity_id=user.id,
        safe_metadata={"status": "created"},
    )

    with pytest.raises(DBAPIError, match="append-only"):
        async with database_session.begin_nested():
            await database_session.execute(
                update(AuditEvent)
                .where(AuditEvent.id == event.id)
                .values(outcome="failed")
            )

    await database_session.refresh(event)
    assert event.outcome == "success"


@pytest.mark.integration
async def test_ai_jobs_are_unique_claimed_and_completed(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 9_100_002)
    entity_id = user.id
    first = await enqueue_ai_job(
        database_session,
        user_id=user.id,
        job_kind="draft_parse",
        entity_id=entity_id,
        operation_key="initial",
        prompt_name="draft",
        prompt_version="draft-v2",
    )
    duplicate = await enqueue_ai_job(
        database_session,
        user_id=user.id,
        job_kind="draft_parse",
        entity_id=entity_id,
        operation_key="initial",
        prompt_name="draft",
        prompt_version="draft-v2",
    )
    claims = await claim_due_ai_jobs(
        database_session, limit=10, max_attempts=3, lease_seconds=60
    )

    assert duplicate.id == first.id
    assert len(claims) == 1
    assert await complete_ai_job(database_session, claims[0]) is True
    assert (
        await claim_due_ai_jobs(
            database_session, limit=10, max_attempts=3, lease_seconds=60
        )
        == []
    )


@pytest.mark.integration
async def test_stale_started_delivery_becomes_unknown_without_retry(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 9_100_003)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    reminder, _ = await create_reminder(
        database_session,
        user.id,
        reminder_type="custom",
        scheduled_at=now,
        deduplication_key="stage8:unknown",
        message="Safe reminder",
    )
    claim = (
        await claim_due_reminders(
            database_session,
            now=now,
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
    )[0]
    assert await mark_reminder_delivery_started(database_session, claim, now=now)

    assert (
        await claim_due_reminders(
            database_session,
            now=now + timedelta(minutes=6),
            limit=10,
            max_attempts=3,
            processing_timeout=timedelta(minutes=5),
        )
        == []
    )
    await database_session.refresh(reminder)
    assert reminder.status == ReminderStatus.DELIVERY_UNKNOWN.value
    assert reminder.delivery_unknown_at == now + timedelta(minutes=6)


@pytest.mark.integration
async def test_cleanup_redacts_terminal_voice_but_preserves_work_item(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 9_100_004)
    old = datetime(2026, 5, 1, 12, tzinfo=UTC)
    note = Note(
        user_id=user.id,
        content="Anonymized voice transcript",
        source="voice",
        telegram_update_id=9_100_004,
        created_at=old,
    )
    database_session.add(note)
    await database_session.flush()
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Preserved structured task",
        source_note_id=note.id,
    )

    result = await run_database_cleanup(
        database_session,
        now=datetime(2026, 7, 22, 12, tzinfo=UTC),
        terminal_transcript_days=30,
        unresolved_transcript_days=90,
        expired_record_days=30,
    )

    await database_session.refresh(note)
    await database_session.refresh(item)
    assert result.redacted_transcripts == 1
    assert note.content is None
    assert note.transcript_redacted_at is not None
    assert item.title == "Preserved structured task"
    assert item.source_note_id == note.id
