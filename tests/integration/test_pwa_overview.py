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
from flowmate.workspaces import activate_workspace
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
    assert inbox["total"] == 3
    assert {entry["kind"] for entry in inbox["items"]} == {
        "draft",
        "note",
        "work_item",
    }
    assert "Foreign private note" not in str(inbox)
    assert "Other workspace note" not in str(inbox)
