from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.users import create_telegram_user
from flowmate.reminders.actions import reminder_revision, snooze_work_item_reminder
from flowmate.task_engine.details import get_work_item_details
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    StaleWorkItemError,
    complete_work_item,
    work_item_revision,
)
from flowmate.task_engine.service import (
    create_work_item,
)


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
