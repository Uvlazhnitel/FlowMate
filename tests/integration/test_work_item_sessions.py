from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import DraftItemType, ManagementAction, ManagementIntent
from flowmate.db.models import (
    Note,
    WorkItem,
    WorkItemActionSession,
    WorkItemEvent,
)
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.action_sessions import (
    create_action_session,
    get_action_session_by_telegram_update,
    get_active_action_session,
)
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.intents import (
    AmbiguousManagementCandidateError,
    find_intent_targets,
    management_update_was_processed,
    resolve_person_candidate,
    resolve_topic_candidate,
)
from flowmate.task_engine.management import (
    complete_work_item,
)
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
)


@pytest.mark.integration
async def test_action_session_expiration_and_transaction_rollback(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_006)
    user_id = user.id
    item = await create_work_item(
        database_session, user_id, item_type="task", title="Rollback task"
    )
    item_id = item.id
    await database_session.commit()

    current = datetime.now(UTC)
    await create_action_session(
        database_session,
        user_id,
        action=WorkItemAction.ADD_NOTE,
        work_item_id=item_id,
        ttl_minutes=1,
        telegram_update_id=830_029,
        now=current,
    )
    assert (
        await get_active_action_session(database_session, user_id, now=current)
        is not None
    )
    assert (
        await get_active_action_session(
            database_session, user_id, now=current + timedelta(minutes=2)
        )
        is None
    )
    await database_session.commit()

    await complete_work_item(database_session, user_id, item_id, 830_030)
    await database_session.rollback()
    refreshed = await database_session.get(WorkItem, item_id)
    assert refreshed is not None
    assert refreshed.status == "inbox"
    assert (
        await database_session.scalar(
            select(func.count(WorkItemEvent.id)).where(
                WorkItemEvent.telegram_update_id == 830_030
            )
        )
        == 0
    )
    assert (
        await database_session.scalar(
            select(func.count(Note.id)).where(Note.user_id == user_id)
        )
        == 0
    )


@pytest.mark.integration
async def test_action_session_creation_is_idempotent_and_owned(
    database_session: AsyncSession,
) -> None:
    owner = await create_telegram_user(database_session, 630_007)
    other = await create_telegram_user(database_session, 630_008)
    item = await create_work_item(
        database_session, owner.id, item_type="task", title="Select me"
    )

    first = await create_action_session(
        database_session,
        owner.id,
        action=WorkItemAction.ADD_NOTE,
        work_item_id=item.id,
        ttl_minutes=30,
        telegram_update_id=830_040,
    )
    duplicate = await create_action_session(
        database_session,
        owner.id,
        action=WorkItemAction.RESCHEDULE,
        work_item_id=item.id,
        ttl_minutes=30,
        telegram_update_id=830_040,
    )

    assert duplicate.id == first.id
    assert duplicate.action == WorkItemAction.ADD_NOTE.value
    assert (
        await database_session.scalar(
            select(func.count(WorkItemActionSession.id)).where(
                WorkItemActionSession.telegram_update_id == 830_040
            )
        )
        == 1
    )
    assert (
        await get_action_session_by_telegram_update(database_session, other.id, 830_040)
        is None
    )
    assert await management_update_was_processed(database_session, 630_007, 830_040)
    assert not await management_update_was_processed(database_session, 630_008, 830_040)


@pytest.mark.integration
async def test_intent_service_prevents_ambiguous_resolution(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_009)
    other = await create_telegram_user(database_session, 630_010)
    first = await create_work_item(
        database_session, user.id, item_type="task", title="Release scope A"
    )
    second = await create_work_item(
        database_session, user.id, item_type="task", title="Release scope B"
    )
    await create_topic(database_session, user.id, "Alpha", aliases=["release"])
    await create_topic(database_session, user.id, "Beta", aliases=["release"])
    await create_person(database_session, user.id, "Alex", aliases=["lead"])
    await create_person(database_session, user.id, "Alexa", aliases=["lead"])
    intent = ManagementIntent(
        action=ManagementAction.COMPLETE,
        target_type=DraftItemType.TASK,
        record_query="Release scope",
        contextual_reference=False,
        person_candidate=None,
        topic_candidate=None,
        note_text=None,
        temporal_candidate=None,
        missing_fields=[],
        ambiguities=[],
        confidence=0.95,
    )

    assert {
        item.id for item in await find_intent_targets(database_session, user.id, intent)
    } == {first.id, second.id}
    assert await find_intent_targets(database_session, other.id, intent) == []
    with pytest.raises(AmbiguousManagementCandidateError):
        await resolve_topic_candidate(database_session, user.id, "release")
    with pytest.raises(AmbiguousManagementCandidateError):
        await resolve_person_candidate(database_session, user.id, "lead")
