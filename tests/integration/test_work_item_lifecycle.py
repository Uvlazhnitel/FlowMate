from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import (
    WorkItemEvent,
    WorkItemRelation,
)
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.enums import PlannerStatus, WorkItemType
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    add_work_item_note,
    cancel_work_item,
    change_planner_status,
    change_work_item_person,
    change_work_item_topic,
    complete_work_item,
    create_follow_up_from_waiting,
    mark_waiting_received,
    reopen_work_item,
    reschedule_work_item,
    sync_planner_status,
    work_item_revision,
)
from flowmate.task_engine.queries import (
    find_matching_work_items,
)
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
    list_linked_notes,
    list_people_for_work_item,
    list_work_item_events,
)


@pytest.mark.integration
async def test_state_transitions_reschedule_and_idempotency(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_002)
    now = datetime(2026, 7, 21, 9, tzinfo=UTC)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Finish report",
        due_at=now,
    )

    completed = await complete_work_item(
        database_session, user.id, item.id, 830_001, now=now
    )
    duplicate = await complete_work_item(
        database_session, user.id, item.id, 830_001, now=now
    )
    assert completed.changed is True
    assert duplicate.changed is False
    assert item.status == "done"
    assert item.completed_at == now
    assert (
        await database_session.scalar(
            select(func.count(WorkItemEvent.id)).where(
                WorkItemEvent.telegram_update_id == 830_001
            )
        )
        == 1
    )

    await reopen_work_item(database_session, user.id, item.id, 830_002)
    new_date = now + timedelta(days=5)
    rescheduled = await reschedule_work_item(
        database_session, user.id, item.id, 830_003, new_date
    )
    assert item.status == "inbox"
    assert item.completed_at is None
    assert item.due_at == new_date
    assert rescheduled.event.payload == {
        "field": "due_at",
        "previous": now.isoformat(),
        "new": new_date.isoformat(),
    }
    await cancel_work_item(database_session, user.id, item.id, 830_004)
    assert item.status == "cancelled"
    with pytest.raises(InvalidWorkItemTransitionError):
        await reopen_work_item(database_session, user.id, item.id, 830_005)


@pytest.mark.integration
async def test_planner_queue_is_opt_in_and_preserves_manual_state(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_015)
    now = datetime(2026, 7, 21, 9, tzinfo=UTC)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Manual Planner task",
        due_at=now,
    )
    assert item.planner_status == PlannerStatus.NOT_REQUIRED.value

    await complete_work_item(database_session, user.id, item.id, 830_130, now=now)
    await reopen_work_item(database_session, user.id, item.id, 830_131)
    assert item.planner_status == PlannerStatus.NOT_REQUIRED.value
    await database_session.refresh(item)

    database_session.info["client_action_id"] = uuid4()
    added = await change_planner_status(
        database_session,
        user.id,
        item.id,
        PlannerStatus.NEEDS_TRANSFER,
        expected_revision=work_item_revision(item.updated_at),
    )
    assert added.changed is True
    await database_session.refresh(item)
    database_session.info["client_action_id"] = uuid4()
    transferred = await change_planner_status(
        database_session,
        user.id,
        item.id,
        PlannerStatus.TRANSFERRED,
        expected_revision=work_item_revision(item.updated_at),
        now=now,
    )
    assert transferred.work_item.planner_transferred_at == now

    await reschedule_work_item(
        database_session,
        user.id,
        item.id,
        830_132,
        now + timedelta(days=1),
    )
    assert item.planner_status == PlannerStatus.UPDATE_REQUIRED.value
    await complete_work_item(database_session, user.id, item.id, 830_133, now=now)
    assert item.planner_status == PlannerStatus.NO_LONGER_RELEVANT.value
    await reopen_work_item(database_session, user.id, item.id, 830_134)
    assert item.planner_status == PlannerStatus.UPDATE_REQUIRED.value
    await database_session.refresh(item)

    database_session.info["client_action_id"] = uuid4()
    await change_planner_status(
        database_session,
        user.id,
        item.id,
        PlannerStatus.NOT_REQUIRED,
        expected_revision=work_item_revision(item.updated_at),
    )
    assert item.planner_transferred_at is None
    item.type = WorkItemType.QUESTION.value
    assert (
        await sync_planner_status(database_session, item, reason="type_changed")
        is False
    )
    assert item.planner_status == PlannerStatus.NOT_REQUIRED.value


@pytest.mark.integration
async def test_linked_changes_history_and_user_isolation(
    database_session: AsyncSession,
) -> None:
    owner = await create_telegram_user(database_session, 630_003)
    other = await create_telegram_user(database_session, 630_004)
    item = await create_work_item(
        database_session, owner.id, item_type="question", title="Clarify scope"
    )
    topic = await create_topic(database_session, owner.id, "Release")
    person = await create_person(database_session, owner.id, "Maria")

    await change_work_item_topic(database_session, owner.id, item.id, 830_010, topic.id)
    await change_work_item_person(
        database_session, owner.id, item.id, 830_011, person.id
    )
    _, note = await add_work_item_note(
        database_session, owner.id, item.id, 830_012, "Decision context"
    )

    assert item.topic_id == topic.id
    assert [
        value.id
        for value in await list_people_for_work_item(
            database_session, owner.id, item.id
        )
    ] == [person.id]
    assert [
        value.id
        for value in await list_linked_notes(
            database_session, owner.id, "work_item", item.id
        )
    ] == [note.id]
    events = await list_work_item_events(database_session, owner.id, item.id)
    assert [event.event_type for event in events][-3:] == [
        "topic_changed",
        "person_changed",
        "note_added",
    ]
    assert "Decision context" not in str(events[-1].payload)
    assert (
        await find_matching_work_items(database_session, other.id, query="scope") == []
    )
    with pytest.raises(ValueError, match="work item not found"):
        await complete_work_item(database_session, other.id, item.id, 830_013)


@pytest.mark.integration
async def test_waiting_received_creates_one_follow_up_with_context(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_005)
    topic = await create_topic(database_session, user.id, "Contract")
    person = await create_person(database_session, user.id, "Client")
    waiting = await create_work_item(
        database_session,
        user.id,
        item_type="waiting",
        title="Receive signed contract",
        status="waiting",
        topic_id=topic.id,
    )
    await link_person_to_work_item(database_session, user.id, waiting.id, person.id)
    await mark_waiting_received(database_session, user.id, waiting.id, 830_020)
    follow_up, created = await create_follow_up_from_waiting(
        database_session, user.id, waiting.id, 830_021
    )
    duplicate, duplicate_created = await create_follow_up_from_waiting(
        database_session, user.id, waiting.id, 830_022
    )

    assert created is True
    assert duplicate_created is False
    assert duplicate.id == follow_up.id
    assert follow_up.topic_id == topic.id
    assert [
        value.id
        for value in await list_people_for_work_item(
            database_session, user.id, follow_up.id
        )
    ] == [person.id]
    relation = await database_session.scalar(
        select(WorkItemRelation).where(
            WorkItemRelation.source_work_item_id == follow_up.id
        )
    )
    assert relation is not None
    assert relation.relation_type == "created_from"
    assert relation.target_work_item_id == waiting.id
