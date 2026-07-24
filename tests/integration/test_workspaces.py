from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import Topic, WorkItem
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.service import create_topic, create_work_item, list_work_items
from flowmate.workspace_service import (
    WorkspaceSwitchBlockedError,
    switch_workspace,
)
from flowmate.workspaces import activate_workspace


@pytest.mark.integration
async def test_workspaces_isolate_topics_and_work_items(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 880_001)
    activate_workspace(
        database_session,
        user_id=user.id,
        workspace="personal",
    )
    personal_topic = await create_topic(database_session, user.id, "Польша")
    personal_item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Личная задача",
        topic_id=personal_topic.id,
        due_at=datetime(2026, 8, 7, 9, tzinfo=UTC),
    )

    await switch_workspace(database_session, user.id, "work")
    assert await list_work_items(database_session, user.id) == []
    assert (
        await database_session.scalar(
            select(Topic).where(Topic.id == personal_topic.id)
        )
        is None
    )

    work_topic = await create_topic(database_session, user.id, "Польша")
    work_item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Рабочая задача",
        topic_id=work_topic.id,
    )
    visible = await list_work_items(database_session, user.id)
    assert [item.id for item in visible] == [work_item.id]
    assert work_item.workspace == "work"

    all_items = list(
        await database_session.scalars(
            select(WorkItem)
            .where(WorkItem.user_id == user.id)
            .execution_options(include_all_workspaces=True)
        )
    )
    assert {item.id for item in all_items} == {personal_item.id, work_item.id}


@pytest.mark.integration
async def test_workspace_switch_is_blocked_by_open_draft(
    database_session: AsyncSession,
) -> None:
    from flowmate.db.drafts import create_parsing_draft
    from flowmate.db.notes import create_note_idempotently

    user = await create_telegram_user(database_session, 880_002)
    activate_workspace(
        database_session,
        user_id=user.id,
        workspace="personal",
    )
    note, _ = await create_note_idempotently(
        database_session,
        user_id=user.id,
        content="Незавершённая запись",
        source="text",
        telegram_update_id=980_002,
    )
    await create_parsing_draft(
        database_session,
        user_id=user.id,
        source_note_id=note.id,
        ttl_hours=24,
    )

    with pytest.raises(WorkspaceSwitchBlockedError) as error:
        await switch_workspace(database_session, user.id, "work")
    assert error.value.blocker.code == "active_draft"
    assert user.active_workspace == "personal"
