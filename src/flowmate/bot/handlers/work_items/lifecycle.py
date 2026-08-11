from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import WorkItem
from flowmate.task_engine.management import (
    cancel_work_item,
    complete_work_item,
    create_follow_up_from_waiting,
    mark_follow_up_replied,
    mark_waiting_received,
    reopen_work_item,
)

LIFECYCLE_ACTIONS = {"c", "x", "o", "a", "g", "f"}


async def execute_lifecycle_action(
    session: AsyncSession,
    *,
    action: str,
    user_id: UUID,
    item: WorkItem,
    telegram_update_id: int,
    expected_revision: int,
) -> tuple[bool | None, str]:
    if action == "c":
        result = await complete_work_item(
            session,
            user_id,
            item.id,
            telegram_update_id,
            expected_revision=expected_revision,
        )
        return result.changed, f"Выполнено: {item.title}"
    if action == "x":
        result = await cancel_work_item(
            session,
            user_id,
            item.id,
            telegram_update_id,
            expected_revision=expected_revision,
        )
        return result.changed, "Запись отменена."
    if action == "o":
        result = await reopen_work_item(
            session,
            user_id,
            item.id,
            telegram_update_id,
            expected_revision=expected_revision,
        )
        return result.changed, "Запись возвращена во входящие."
    if action == "a":
        result = await mark_follow_up_replied(
            session,
            user_id,
            item.id,
            telegram_update_id,
            expected_revision=expected_revision,
        )
        return result.changed, "Ответ получен, follow-up завершён."
    if action == "g":
        result = await mark_waiting_received(
            session,
            user_id,
            item.id,
            telegram_update_id,
            expected_revision=expected_revision,
        )
        return result.changed, "Результат получен."
    if action == "f":
        _, created = await create_follow_up_from_waiting(
            session,
            user_id,
            item.id,
            telegram_update_id,
            require_received=False,
            expected_revision=expected_revision,
        )
        return None, "Follow-up создан." if created else "Follow-up уже создан."
    raise ValueError("unsupported lifecycle action")
