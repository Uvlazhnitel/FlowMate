from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.handlers.navigation import (
    ExpiredListError,
    build_navigation_page,
    build_search_page,
)
from flowmate.db.models import WorkItemActionSession
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.action_sessions import (
    create_action_session,
    finish_action_session,
    get_action_session_by_telegram_update,
    get_search_session_for_user,
)
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.intents import management_update_was_processed
from flowmate.task_engine.search import (
    WorkItemSearchFilters,
    search_stale_contacts,
    search_work_items,
)
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
)


@pytest.mark.integration
async def test_work_item_pages_have_stable_boundaries_and_enrichment(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_001)
    other = await create_telegram_user(database_session, 650_002)
    topic = await create_topic(database_session, user.id, "Testing")
    person = await create_person(database_session, user.id, "Антон")
    for index in range(7):
        item = await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title=f"Task {index}",
            topic_id=topic.id if index == 0 else None,
        )
        if index == 0:
            await link_person_to_work_item(
                database_session,
                user.id,
                item.id,
                person.id,
            )
    await create_work_item(
        database_session,
        other.id,
        item_type="task",
        title="Private other-user task",
    )

    first = await build_navigation_page(
        database_session,
        user.id,
        view="t",
        page=0,
        timezone=ZoneInfo("UTC"),
    )
    last = await build_navigation_page(
        database_session,
        user.id,
        view="t",
        page=1,
        timezone=ZoneInfo("UTC"),
    )

    assert (
        len(
            [
                row
                for row in first.keyboard.inline_keyboard
                if row[0].text.endswith("Открыть")
            ]
        )
        == 5
    )
    assert any(
        button.text == "Вперёд"
        for row in first.keyboard.inline_keyboard
        for button in row
    )
    assert "Private other-user task" not in first.text + last.text
    assert "Testing" in first.text + last.text
    assert "Антон" in first.text + last.text
    assert not any(
        button.text == "Вперёд"
        for row in last.keyboard.inline_keyboard
        for button in row
    )
    with pytest.raises(ExpiredListError):
        await build_navigation_page(
            database_session,
            user.id,
            view="t",
            page=2,
            timezone=ZoneInfo("UTC"),
        )


@pytest.mark.integration
async def test_search_includes_completed_records_and_is_user_isolated(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_003)
    other = await create_telegram_user(database_session, 650_004)
    for index in range(6):
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title=f"Release checklist {index}",
            status="done",
            completed_at=datetime.now(UTC),
        )
    await create_work_item(
        database_session,
        other.id,
        item_type="task",
        title="Private release checklist",
    )
    action = await create_action_session(
        database_session,
        user.id,
        action=WorkItemAction.SEARCH,
        ttl_minutes=30,
        context={"query": "release"},
        telegram_update_id=950_001,
    )
    await finish_action_session(database_session, action)

    page = await build_search_page(
        database_session,
        user.id,
        action,
        page=0,
        timezone=ZoneInfo("UTC"),
    )

    assert "Release checklist" in page.text
    assert "Private release checklist" not in page.text
    assert f"lq:{action.id}" in str(page.keyboard.model_dump())
    assert (
        await get_search_session_for_user(database_session, other.id, action.id) is None
    )


@pytest.mark.integration
async def test_expired_search_session_is_not_available(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_005)
    now = datetime.now(UTC)
    action = WorkItemActionSession(
        user_id=user.id,
        action=WorkItemAction.SEARCH.value,
        status="completed",
        context={"query": "task"},
        created_at=now - timedelta(hours=1),
        updated_at=now - timedelta(hours=1),
        expires_at=now - timedelta(seconds=1),
    )
    database_session.add(action)
    await database_session.flush()

    assert (
        await get_search_session_for_user(
            database_session,
            user.id,
            action.id,
            now=now,
        )
        is None
    )


@pytest.mark.integration
async def test_search_reply_update_is_deduplicated(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_006)
    action = await create_action_session(
        database_session,
        user.id,
        action=WorkItemAction.SEARCH,
        ttl_minutes=30,
        context={"query": "release", "processed_update_ids": [950_101, 950_102]},
        telegram_update_id=950_101,
    )
    await finish_action_session(database_session, action)

    assert (
        await get_action_session_by_telegram_update(
            database_session,
            user.id,
            950_102,
        )
        == action
    )
    assert user.telegram_user_id is not None
    assert await management_update_was_processed(
        database_session,
        user.telegram_user_id,
        950_102,
    )


@pytest.mark.integration
async def test_structured_search_matches_text_people_topics_aliases_and_owner(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_007)
    other = await create_telegram_user(database_session, 650_008)
    topic = await create_topic(
        database_session,
        user.id,
        "Deployment",
        aliases=["release train"],
    )
    person = await create_person(
        database_session,
        user.id,
        "Антон Иванов",
        aliases=["tony lead"],
    )
    item = await create_work_item(
        database_session,
        user.id,
        item_type="follow_up",
        title="Confirm rollout",
        description="Budget approval is pending",
        topic_id=topic.id,
        next_follow_up_at=datetime(2026, 7, 22, 10, tzinfo=UTC),
    )
    await link_person_to_work_item(database_session, user.id, item.id, person.id)
    await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Budget archive",
        status="done",
        completed_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    await create_work_item(
        database_session,
        other.id,
        item_type="follow_up",
        title="Private rollout",
    )

    by_text = await search_work_items(
        database_session,
        user.id,
        WorkItemSearchFilters(text_query="budget"),
        now=datetime(2026, 7, 22, 12, tzinfo=UTC),
    )
    by_person_alias = await search_work_items(
        database_session,
        user.id,
        WorkItemSearchFilters(text_query="tony"),
        now=datetime(2026, 7, 22, 12, tzinfo=UTC),
    )
    by_topic_alias = await search_work_items(
        database_session,
        user.id,
        WorkItemSearchFilters(text_query="train"),
        now=datetime(2026, 7, 22, 12, tzinfo=UTC),
    )

    assert [value.id for value in by_text] == [item.id]
    assert [value.id for value in by_person_alias] == [item.id]
    assert [value.id for value in by_topic_alias] == [item.id]


@pytest.mark.integration
async def test_structured_search_applies_overdue_date_status_and_stale_contacts(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 650_009)
    old_person = await create_person(database_session, user.id, "Old contact")
    recent_person = await create_person(database_session, user.id, "Recent contact")
    old = await create_work_item(
        database_session,
        user.id,
        item_type="follow_up",
        title="Old follow-up",
        next_follow_up_at=datetime(2026, 7, 1, 9, tzinfo=UTC),
    )
    recent = await create_work_item(
        database_session,
        user.id,
        item_type="question",
        title="Recent question",
        due_at=datetime(2026, 7, 21, 9, tzinfo=UTC),
    )
    future = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Future task",
        due_at=datetime(2026, 8, 2, 9, tzinfo=UTC),
    )
    await link_person_to_work_item(database_session, user.id, old.id, old_person.id)
    await link_person_to_work_item(
        database_session, user.id, recent.id, recent_person.id
    )
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)

    overdue = await search_work_items(
        database_session,
        user.id,
        WorkItemSearchFilters(overdue=True),
        now=now,
    )
    august = await search_work_items(
        database_session,
        user.id,
        WorkItemSearchFilters(
            due_from=datetime(2026, 8, 1, tzinfo=UTC),
            due_to=datetime(2026, 9, 1, tzinfo=UTC),
        ),
        now=now,
    )
    stale = await search_stale_contacts(database_session, user.id)

    assert {value.id for value in overdue} == {old.id, recent.id}
    assert [value.id for value in august] == [future.id]
    assert [value.person.id for value in stale] == [old_person.id, recent_person.id]
