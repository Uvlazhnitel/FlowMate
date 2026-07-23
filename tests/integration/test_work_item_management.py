from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Chat, Message, Update, User
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import DraftItemType, ManagementAction, ManagementIntent
from flowmate.ai.service import DraftParsingService
from flowmate.bot.handlers.notes import (
    MANAGEMENT_ALREADY_PROCESSED_MESSAGE,
    text_note,
)
from flowmate.db.models import (
    Note,
    WorkItem,
    WorkItemActionSession,
    WorkItemEvent,
    WorkItemRelation,
)
from flowmate.db.users import create_telegram_user
from flowmate.reminders.actions import reminder_revision, snooze_work_item_reminder
from flowmate.task_engine.action_sessions import (
    create_action_session,
    get_action_session_by_telegram_update,
    get_active_action_session,
)
from flowmate.task_engine.details import get_work_item_details
from flowmate.task_engine.enums import WorkItemAction, WorkItemType
from flowmate.task_engine.intents import (
    AmbiguousManagementCandidateError,
    find_intent_targets,
    management_update_was_processed,
    resolve_person_candidate,
    resolve_topic_candidate,
)
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    StaleWorkItemError,
    add_work_item_note,
    cancel_work_item,
    change_work_item_person,
    change_work_item_topic,
    complete_work_item,
    create_follow_up_from_waiting,
    mark_waiting_received,
    reopen_work_item,
    reschedule_work_item,
    work_item_revision,
)
from flowmate.task_engine.queries import (
    find_matching_work_items,
    list_follow_ups,
    list_open_questions,
    list_person_counts,
    list_recent_tasks,
    list_today_items,
    list_topic_counts,
    list_waiting_items,
)
from flowmate.task_engine.service import (
    append_work_item_event,
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
    list_linked_notes,
    list_people_for_work_item,
    list_work_item_events,
)


@pytest.mark.integration
async def test_management_lists_and_aggregate_counts(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_001)
    topic = await create_topic(database_session, user.id, "Testing")
    person = await create_person(database_session, user.id, "Anton")
    today = datetime(2026, 7, 21, 12, tzinfo=UTC)
    task = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Prepare tests",
        topic_id=topic.id,
        due_at=today,
    )
    follow_up = await create_work_item(
        database_session,
        user.id,
        item_type="follow_up",
        title="Contact Anton",
        topic_id=topic.id,
        next_follow_up_at=today + timedelta(hours=1),
    )
    waiting = await create_work_item(
        database_session,
        user.id,
        item_type="waiting",
        title="Wait for response",
        status="waiting",
        waiting_since=today - timedelta(days=2),
    )
    question = await create_work_item(
        database_session,
        user.id,
        item_type="question",
        title="Ask about rollout",
    )
    for item in (follow_up, waiting, question):
        await link_person_to_work_item(database_session, user.id, item.id, person.id)

    assert [item.id for item in await list_recent_tasks(database_session, user.id)] == [
        task.id
    ]
    assert [item.id for item in await list_follow_ups(database_session, user.id)] == [
        follow_up.id
    ]
    assert [
        item.id for item in await list_waiting_items(database_session, user.id)
    ] == [waiting.id]
    assert [
        item.id for item in await list_open_questions(database_session, user.id)
    ] == [question.id]
    today_items = await list_today_items(
        database_session,
        user.id,
        start=today.replace(hour=0),
        end=today.replace(hour=0) + timedelta(days=1),
    )
    assert [item.id for item in today_items] == [task.id, follow_up.id]
    topic_counts = await list_topic_counts(database_session, user.id)
    assert [(value.topic.id, value.open_count) for value in topic_counts] == [
        (topic.id, 2)
    ]
    person_counts = await list_person_counts(database_session, user.id)
    assert (
        person_counts[0].open_item_count,
        person_counts[0].follow_up_count,
        person_counts[0].waiting_count,
        person_counts[0].question_count,
    ) == (3, 1, 1, 1)


@pytest.mark.integration
async def test_people_directory_scopes_and_recent_boundary(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_101)
    other = await create_telegram_user(database_session, 630_102)
    now = datetime(2026, 7, 23, 12, tzinfo=UTC)
    open_names: set[str] = set()
    for index, item_type in enumerate(WorkItemType):
        person = await create_person(
            database_session,
            user.id,
            f"Open {item_type.value}",
        )
        item = await create_work_item(
            database_session,
            user.id,
            item_type=item_type,
            title=f"Open item {index}",
        )
        await link_person_to_work_item(database_session, user.id, item.id, person.id)
        open_names.add(person.display_name)

    recent = await create_person(database_session, user.id, "Recent person")
    recent.updated_at = now - timedelta(days=90)
    stale = await create_person(database_session, user.id, "Stale person")
    stale.updated_at = now - timedelta(days=90, seconds=1)
    completed = await create_person(database_session, user.id, "Completed person")
    completed_item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Completed task",
        status="done",
        completed_at=now,
    )
    await link_person_to_work_item(
        database_session,
        user.id,
        completed_item.id,
        completed.id,
    )
    inactive = await create_person(database_session, user.id, "Inactive person")
    inactive.is_active = False
    inactive_item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Inactive person's task",
    )
    await link_person_to_work_item(
        database_session,
        user.id,
        inactive_item.id,
        inactive.id,
    )
    foreign = await create_person(database_session, other.id, "Foreign person")
    foreign_item = await create_work_item(
        database_session,
        other.id,
        item_type="task",
        title="Foreign task",
    )
    await link_person_to_work_item(
        database_session,
        other.id,
        foreign_item.id,
        foreign.id,
    )
    await database_session.flush()

    work = await list_person_counts(database_session, user.id, now=now, limit=20)
    assert {value.person.display_name for value in work} == open_names
    assert all(value.open_item_count == 1 for value in work)

    recent_values = await list_person_counts(
        database_session,
        user.id,
        scope="recent",
        now=now,
        limit=20,
    )
    assert {value.person.display_name for value in recent_values} == {
        *open_names,
        "Completed person",
        "Recent person",
    }

    all_values = await list_person_counts(
        database_session,
        user.id,
        scope="all",
        query="person",
        now=now,
        limit=1,
    )
    assert len(all_values) == 1
    assert all_values[0].person.display_name in {
        "Completed person",
        "Recent person",
        "Stale person",
    }


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


@pytest.mark.integration
async def test_work_item_details_are_owned_limited_and_enriched(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_012)
    other = await create_telegram_user(database_session, 630_013)
    topic = await create_topic(database_session, user.id, "Testing")
    person = await create_person(database_session, user.id, "Антон")
    due_at = datetime.now(UTC) + timedelta(days=1)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Detail task",
        description="Useful description",
        topic_id=topic.id,
        due_at=due_at,
    )
    await link_person_to_work_item(database_session, user.id, item.id, person.id)
    for index in range(4):
        await add_work_item_note(
            database_session,
            user.id,
            item.id,
            830_100 + index,
            f"Note {index}",
        )
    for index in range(6):
        await append_work_item_event(
            database_session,
            user.id,
            item.id,
            "updated",
            payload={"index": index},
        )

    details = await get_work_item_details(database_session, user.id, item.id)

    assert details is not None
    assert details.topic_name == "Testing"
    assert details.person_names == ("Антон",)
    assert len(details.notes) == 3
    assert len(details.events) == 5
    assert details.nearest_reminder is not None
    assert await get_work_item_details(database_session, other.id, item.id) is None


@pytest.mark.integration
async def test_stale_work_item_and_reminder_revisions_are_rejected(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_014)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Revision task",
        due_at=datetime.now(UTC) + timedelta(days=1),
    )
    item_revision = work_item_revision(item.updated_at)
    item.updated_at = item.updated_at + timedelta(seconds=1)
    await database_session.flush()
    with pytest.raises(StaleWorkItemError):
        await complete_work_item(
            database_session,
            user.id,
            item.id,
            830_120,
            expected_revision=item_revision,
        )

    details = await get_work_item_details(database_session, user.id, item.id)
    assert details is not None and details.nearest_reminder is not None
    reminder = details.nearest_reminder
    stale_reminder_revision = reminder_revision(reminder)
    await snooze_work_item_reminder(
        database_session,
        user.id,
        reminder.id,
        830_121,
        duration=timedelta(hours=1),
        expected_revision=stale_reminder_revision,
    )
    with pytest.raises(InvalidWorkItemTransitionError):
        await snooze_work_item_reminder(
            database_session,
            user.id,
            reminder.id,
            830_122,
            duration=timedelta(hours=1),
            expected_revision=stale_reminder_revision,
        )


@pytest.mark.integration
async def test_repeated_force_reply_update_stops_before_ai_and_new_note(
    database_session: AsyncSession,
) -> None:
    telegram_user_id = 630_011
    update_id = 830_050
    user = await create_telegram_user(database_session, telegram_user_id)
    user_id = user.id
    item = await create_work_item(
        database_session, user_id, item_type="task", title="Document result"
    )
    await add_work_item_note(
        database_session,
        user_id,
        item.id,
        update_id,
        "Private reply contents",
    )
    await database_session.commit()
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=telegram_user_id, type="private"),
        from_user=User(
            id=telegram_user_id,
            is_bot=False,
            first_name="Test",
        ),
        text="Private reply contents",
    )
    update = Update(update_id=update_id, message=message)
    service = MagicMock(spec=DraftParsingService)
    service.parse_text = AsyncMock()
    note_count = await database_session.scalar(
        select(func.count(Note.id)).where(Note.user_id == user_id)
    )

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await text_note(
            message,
            update,
            database_session,
            cast(DraftParsingService, service),
        )

    service.parse_text.assert_not_awaited()
    answer.assert_awaited_once_with(MANAGEMENT_ALREADY_PROCESSED_MESSAGE)
    assert (
        await database_session.scalar(
            select(func.count(Note.id)).where(Note.user_id == user_id)
        )
        == note_count
    )
    assert (
        await database_session.scalar(
            select(func.count(WorkItemEvent.id)).where(
                WorkItemEvent.telegram_update_id == update_id
            )
        )
        == 1
    )
