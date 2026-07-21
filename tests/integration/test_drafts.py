from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.drafts import (
    claim_update,
    create_parsing_draft,
    get_active_draft_for_user,
    get_draft_by_processed_update,
    get_draft_for_user,
    load_analysis,
    replace_draft_analysis,
    transition_draft,
)
from flowmate.db.models import DraftItemRecord, DraftSession, Note, User
from flowmate.db.notes import create_note_idempotently
from flowmate.db.users import create_telegram_user
from flowmate.drafts.questions import next_clarification_question
from tests.ai_factories import make_analysis_result, make_draft_item, make_parse_result


async def make_source_note(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    update_id: int,
) -> tuple[User, Note]:
    user = await create_telegram_user(session, telegram_user_id)
    note, _ = await create_note_idempotently(
        session,
        user_id=user.id,
        content="Draft source",
        source="text",
        telegram_update_id=update_id,
    )
    return user, note


@pytest.mark.integration
async def test_draft_persists_multiple_items_and_payload(
    database_session: AsyncSession,
) -> None:
    user, note = await make_source_note(
        database_session,
        telegram_user_id=510_001,
        update_id=610_001,
    )
    draft = await create_parsing_draft(
        database_session,
        user_id=user.id,
        source_note_id=note.id,
        ttl_hours=24,
    )
    analysis = make_analysis_result(
        make_parse_result(
            [
                make_draft_item(title="First item"),
                make_draft_item(title="Second item", confidence=0.4),
            ]
        )
    )
    await replace_draft_analysis(
        database_session,
        draft,
        analysis,
        question=next_clarification_question(analysis),
        ttl_hours=24,
    )
    await database_session.flush()
    loaded = await get_draft_for_user(
        database_session,
        draft.id,
        user.id,
    )

    assert loaded is not None
    assert loaded.status == "needs_clarification"
    assert [item.title for item in loaded.items] == ["First item", "Second item"]
    assert load_analysis(loaded) == analysis
    assert await database_session.scalar(select(func.count(DraftItemRecord.id))) == 2


@pytest.mark.integration
async def test_only_one_open_draft_per_user(database_session: AsyncSession) -> None:
    user, first_note = await make_source_note(
        database_session,
        telegram_user_id=510_002,
        update_id=610_002,
    )
    second_note, _ = await create_note_idempotently(
        database_session,
        user_id=user.id,
        content="Second source",
        source="text",
        telegram_update_id=610_003,
    )
    await create_parsing_draft(
        database_session,
        user_id=user.id,
        source_note_id=first_note.id,
        ttl_hours=24,
    )

    with pytest.raises(IntegrityError):
        async with database_session.begin_nested():
            await create_parsing_draft(
                database_session,
                user_id=user.id,
                source_note_id=second_note.id,
                ttl_hours=24,
            )


@pytest.mark.integration
async def test_draft_claim_is_idempotent_and_user_isolated(
    database_session: AsyncSession,
) -> None:
    owner, note = await make_source_note(
        database_session,
        telegram_user_id=510_003,
        update_id=610_004,
    )
    other, _ = await make_source_note(
        database_session,
        telegram_user_id=510_004,
        update_id=610_005,
    )
    draft = await create_parsing_draft(
        database_session,
        user_id=owner.id,
        source_note_id=note.id,
        ttl_hours=24,
    )
    draft.status = "needs_clarification"
    await database_session.flush()

    missing = await get_draft_for_user(
        database_session,
        draft.id,
        other.id,
    )
    first, _ = await claim_update(
        database_session,
        draft_id=draft.id,
        user_id=owner.id,
        update_id=710_001,
        ttl_hours=24,
    )
    draft.processing_update_id = None
    duplicate, _ = await claim_update(
        database_session,
        draft_id=draft.id,
        user_id=owner.id,
        update_id=710_001,
        ttl_hours=24,
    )

    assert missing is None
    assert first == "claimed"
    assert duplicate == "duplicate"
    processed = await get_draft_by_processed_update(
        database_session,
        user_id=owner.id,
        update_id=710_001,
    )
    assert processed is not None
    assert processed.id == draft.id


@pytest.mark.integration
async def test_expiration_and_confirmation_are_status_only(
    database_session: AsyncSession,
) -> None:
    user, note = await make_source_note(
        database_session,
        telegram_user_id=510_005,
        update_id=610_006,
    )
    now = datetime.now(UTC)
    draft = await create_parsing_draft(
        database_session,
        user_id=user.id,
        source_note_id=note.id,
        ttl_hours=1,
        now=now,
    )
    active = await get_active_draft_for_user(
        database_session,
        user.id,
        now=now + timedelta(hours=2),
    )
    assert active is None
    assert draft.status == "expired"

    draft.status = "ready"
    draft.expires_at = now + timedelta(hours=3)
    await transition_draft(database_session, draft, "confirmed")
    assert draft.status == "confirmed"
    assert await database_session.scalar(select(func.count(DraftSession.id))) == 1
