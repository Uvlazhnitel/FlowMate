from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.drafts import create_parsing_draft, replace_draft_analysis
from flowmate.db.models import Note
from flowmate.db.notes import create_note_idempotently
from flowmate.db.users import create_telegram_user
from flowmate.reminders.preferences import NotificationDefaults, effective_preferences
from flowmate.task_engine.overview import OVERVIEW_LIMIT, overview_snapshot
from flowmate.task_engine.service import create_topic, create_work_item
from flowmate.workspaces import activate_workspace, workspace_context
from tests.ai_factories import make_analysis_result, make_draft_item, make_parse_result


@pytest.mark.integration
async def test_overview_snapshot_is_bounded_ordered_and_workspace_safe(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 9_991_001)
    foreign = await create_telegram_user(database_session, 9_991_002)
    activate_workspace(database_session, user_id=user.id, workspace="personal")
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    preferences = effective_preferences(
        None,
        NotificationDefaults(
            timezone="UTC",
            morning_digest_time=time(8),
            evening_digest_time=time(18),
            quiet_hours_start=time(22),
            quiet_hours_end=time(7),
            snooze_minutes=60,
        ),
    )
    topic = await create_topic(database_session, user.id, "Delivery")
    expected_today_titles = [
        "Urgent overdue",
        "High overdue",
        "Normal overdue",
        "Today 1",
        "Today 2",
        "Today 3",
        "Today 4",
        "Today 5",
    ]
    urgent = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title=expected_today_titles[0],
        status="active",
        priority="urgent",
        due_at=now - timedelta(hours=1),
    )
    for index, (title, priority) in enumerate(
        zip(expected_today_titles[1:3], ("high", "normal"), strict=True), start=2
    ):
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title=title,
            status="active",
            priority=priority,
            topic_id=topic.id,
            due_at=now - timedelta(hours=index),
        )
    for index in range(1, 7):
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title=f"Today {index}",
            status="active",
            topic_id=topic.id,
            due_at=now + timedelta(hours=index),
        )
    for index in range(9):
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title=f"Tomorrow {index + 1}",
            status="active",
            topic_id=topic.id,
            due_at=now + timedelta(days=1, minutes=index),
        )
    source, _ = await create_note_idempotently(
        database_session,
        user_id=user.id,
        content="Prepare overview draft",
        source="text",
        telegram_update_id=9_991_003,
    )
    draft = await create_parsing_draft(
        database_session,
        user_id=user.id,
        source_note_id=source.id,
        ttl_hours=24,
    )
    await replace_draft_analysis(
        database_session,
        draft,
        make_analysis_result(
            make_parse_result([make_draft_item(title="Draft preview")])
        ),
        question=None,
        ttl_hours=24,
    )
    database_session.add(
        Note(user_id=user.id, content="Standalone preview", source="manual")
    )
    database_session.add(
        Note(user_id=foreign.id, content="Foreign private note", source="manual")
    )
    await database_session.flush()
    activate_workspace(database_session, user_id=user.id, workspace="work")
    database_session.add(
        Note(user_id=user.id, content="Other workspace note", source="manual")
    )
    await database_session.flush()
    activate_workspace(database_session, user_id=user.id, workspace="personal")

    overview = await overview_snapshot(
        database_session,
        user.id,
        now=now,
        preferences=preferences,
        low_confidence_threshold=0.8,
    )

    today = overview["today"]
    assert isinstance(today, dict)
    assert today["total"] == 9
    assert today["has_more"] is True
    assert today["completed_items"] == []
    assert today["completed_total"] == 0
    assert today["completed_has_more"] is False
    assert [entry["item"].title for entry in today["items"]] == expected_today_titles
    overlap = next(entry for entry in today["items"] if entry["item"].id == urgent.id)
    assert overlap["needs_inbox"] is True

    tomorrow = overview["tomorrow"]
    assert isinstance(tomorrow, dict)
    assert tomorrow["total"] == 9
    assert tomorrow["has_more"] is True
    assert len(tomorrow["items"]) == OVERVIEW_LIMIT

    inbox = overview["inbox"]
    assert isinstance(inbox, dict)
    assert inbox["total"] == 4
    assert {entry["kind"] for entry in inbox["items"]} == {
        "draft",
        "note",
        "work_item",
    }
    assert "Foreign private note" not in str(inbox)
    assert "Other workspace note" in str(inbox)
    assert overview["workspace_counts"] == {
        "all": 21,
        "work": 1,
        "personal": 20,
    }

    personal_overview = await overview_snapshot(
        database_session,
        user.id,
        now=now,
        preferences=preferences,
        low_confidence_threshold=0.8,
        workspace_scope="personal",
    )
    personal_inbox = personal_overview["inbox"]
    assert isinstance(personal_inbox, dict)
    assert personal_inbox["total"] == 3
    assert "Other workspace note" not in str(personal_inbox)


@pytest.mark.integration
async def test_overview_completed_today_search_and_workspace_scope(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 9_991_011)
    foreign = await create_telegram_user(database_session, 9_991_012)
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    preferences = effective_preferences(
        None,
        NotificationDefaults(
            timezone="UTC",
            morning_digest_time=time(8),
            evening_digest_time=time(18),
            quiet_hours_start=time(22),
            quiet_hours_end=time(7),
            snooze_minutes=60,
        ),
    )

    with workspace_context(database_session, user_id=user.id, workspace="personal"):
        for index in range(9):
            item = await create_work_item(
                database_session,
                user.id,
                item_type="task",
                title=f"Needle completed {index}",
                status="done",
                due_at=now - timedelta(hours=index + 1),
            )
            item.completed_at = now - timedelta(minutes=index)
        old = await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title="Needle old completion",
            status="done",
            due_at=now - timedelta(days=1),
        )
        old.completed_at = now - timedelta(days=1)
        inbox_done = await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title="Needle inbox completion",
            status="done",
        )
        inbox_done.completed_at = now
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title="Needle active inbox",
            status="inbox",
        )

    with workspace_context(database_session, user_id=user.id, workspace="work"):
        tomorrow_done = await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title="Needle work tomorrow",
            status="done",
            due_at=now + timedelta(days=1),
        )
        tomorrow_done.completed_at = now
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title="Unrelated active",
            status="active",
            due_at=now,
        )

    foreign_done = await create_work_item(
        database_session,
        foreign.id,
        item_type="task",
        title="Needle foreign",
        status="done",
        due_at=now,
    )
    foreign_done.completed_at = now
    await database_session.flush()

    overview = await overview_snapshot(
        database_session,
        user.id,
        now=now,
        preferences=preferences,
        low_confidence_threshold=0.8,
        search_query="needle",
    )
    today = overview["today"]
    tomorrow = overview["tomorrow"]
    inbox = overview["inbox"]
    assert isinstance(today, dict)
    assert isinstance(tomorrow, dict)
    assert isinstance(inbox, dict)
    assert today["items"] == []
    assert today["completed_total"] == 9
    assert len(today["completed_items"]) == OVERVIEW_LIMIT
    assert today["completed_has_more"] is True
    assert tomorrow["completed_total"] == 1
    assert inbox["completed_total"] == 1
    assert any(entry["title"] == "Needle active inbox" for entry in inbox["items"])
    assert "Needle old completion" not in str(overview)
    assert "Needle foreign" not in str(overview)
    assert overview["workspace_counts"] == {
        "all": 12,
        "work": 1,
        "personal": 11,
    }

    work_only = await overview_snapshot(
        database_session,
        user.id,
        now=now,
        preferences=preferences,
        low_confidence_threshold=0.8,
        workspace_scope="work",
        search_query="needle",
    )
    scoped_tomorrow = work_only["tomorrow"]
    assert isinstance(scoped_tomorrow, dict)
    assert scoped_tomorrow["completed_total"] == 1
    scoped_today = work_only["today"]
    assert isinstance(scoped_today, dict)
    assert scoped_today["completed_total"] == 0
