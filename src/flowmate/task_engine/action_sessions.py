from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import WorkItemActionSession
from flowmate.task_engine.enums import WorkItemAction


def action_now() -> datetime:
    return datetime.now(UTC)


async def get_active_action_session(
    session: AsyncSession,
    user_id: UUID,
    *,
    for_update: bool = False,
    now: datetime | None = None,
) -> WorkItemActionSession | None:
    statement = select(WorkItemActionSession).where(
        WorkItemActionSession.user_id == user_id,
        WorkItemActionSession.status == "open",
    )
    if for_update:
        statement = statement.with_for_update()
    value = (await session.scalars(statement)).one_or_none()
    if value is not None and value.expires_at <= (now or action_now()):
        value.status = "expired"
        await session.flush()
        return None
    return value


async def get_action_session_for_user(
    session: AsyncSession,
    user_id: UUID,
    action_session_id: UUID,
    *,
    for_update: bool = False,
) -> WorkItemActionSession | None:
    statement = select(WorkItemActionSession).where(
        WorkItemActionSession.id == action_session_id,
        WorkItemActionSession.user_id == user_id,
        WorkItemActionSession.status == "open",
    )
    if for_update:
        statement = statement.with_for_update()
    value = (await session.scalars(statement)).one_or_none()
    if value is not None and value.expires_at <= action_now():
        value.status = "expired"
        await session.flush()
        return None
    return value


async def get_search_session_for_user(
    session: AsyncSession,
    user_id: UUID,
    action_session_id: UUID,
    *,
    now: datetime | None = None,
) -> WorkItemActionSession | None:
    value = await session.scalar(
        select(WorkItemActionSession).where(
            WorkItemActionSession.id == action_session_id,
            WorkItemActionSession.user_id == user_id,
            WorkItemActionSession.action == WorkItemAction.SEARCH.value,
            WorkItemActionSession.status == "completed",
        )
    )
    if value is None or value.expires_at <= (now or action_now()):
        return None
    return value


async def get_action_session_by_telegram_update(
    session: AsyncSession,
    user_id: UUID,
    telegram_update_id: int,
) -> WorkItemActionSession | None:
    if telegram_update_id <= 0:
        raise ValueError("telegram_update_id must be positive")
    return (
        await session.scalars(
            select(WorkItemActionSession).where(
                WorkItemActionSession.user_id == user_id,
                or_(
                    WorkItemActionSession.telegram_update_id == telegram_update_id,
                    WorkItemActionSession.context.contains(
                        {"processed_update_ids": [telegram_update_id]}
                    ),
                ),
            )
        )
    ).one_or_none()


async def create_action_session(
    session: AsyncSession,
    user_id: UUID,
    *,
    action: WorkItemAction,
    ttl_minutes: int,
    work_item_id: UUID | None = None,
    context: dict[str, Any] | None = None,
    telegram_update_id: int | None = None,
    now: datetime | None = None,
) -> WorkItemActionSession:
    if telegram_update_id is not None:
        previous = await get_action_session_by_telegram_update(
            session,
            user_id,
            telegram_update_id,
        )
        if previous is not None:
            return previous
    current = now or action_now()
    existing = await get_active_action_session(session, user_id, for_update=True)
    if existing is not None:
        existing.status = "cancelled"
        await session.flush()
    value = WorkItemActionSession(
        user_id=user_id,
        work_item_id=work_item_id,
        action=action.value,
        context=dict(context or {}),
        telegram_update_id=telegram_update_id,
        expires_at=current + timedelta(minutes=ttl_minutes),
    )
    session.add(value)
    await session.flush()
    return value


async def finish_action_session(
    session: AsyncSession,
    value: WorkItemActionSession,
    status: str = "completed",
) -> None:
    if status not in {"completed", "cancelled", "expired"}:
        raise ValueError("invalid action session status")
    value.status = status
    await session.flush()
