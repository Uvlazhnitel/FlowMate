from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.users import create_telegram_user
from flowmate.task_engine.enums import WorkItemType
from flowmate.task_engine.queries import (
    list_follow_ups,
    list_open_questions,
    list_person_counts,
    list_recent_tasks,
    list_today_items,
    list_topic_counts,
    list_waiting_items,
)
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
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
