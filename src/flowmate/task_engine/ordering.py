from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import WorkItem
from flowmate.reminders.preferences import EffectiveNotificationPreferences
from flowmate.reminders.sync import ReminderPolicy
from flowmate.task_engine.management import MutationResult
from flowmate.task_engine.queries import OPEN_STATUSES, top_level_work_item_filter
from flowmate.task_engine.rescheduling import ReschedulingService


async def move_work_item_keyboard(
    session: AsyncSession,
    user_id: UUID,
    work_item_id: UUID,
    target: Literal["inbox", "today", "tomorrow"],
    *,
    before_id: UUID | None,
    after_id: UUID | None,
    preferences: EffectiveNotificationPreferences,
    reminder_policy: ReminderPolicy,
    expected_revision: int,
    rescheduling_service: ReschedulingService,
) -> MutationResult:
    result = await rescheduling_service.move_to_bucket(
        session,
        user_id,
        work_item_id,
        target,
        preferences=preferences,
        reminder_policy=reminder_policy,
        expected_revision=expected_revision,
    )
    if not result.changed:
        return result
    rows = list(
        await session.scalars(
            select(WorkItem)
            .where(
                WorkItem.user_id == user_id,
                top_level_work_item_filter(),
                WorkItem.status.in_(OPEN_STATUSES),
            )
            .order_by(WorkItem.sort_rank.nulls_last(), WorkItem.updated_at, WorkItem.id)
            .with_for_update()
        )
    )
    rows = [row for row in rows if row.id != work_item_id]
    insert_at = len(rows)
    anchor = before_id or after_id
    if anchor is not None:
        for index, row in enumerate(rows):
            if row.id == anchor:
                insert_at = index if before_id is not None else index + 1
                break
    rows.insert(insert_at, result.work_item)
    for rank, row in enumerate(rows, start=1):
        row.sort_rank = rank * 1000
    return result
