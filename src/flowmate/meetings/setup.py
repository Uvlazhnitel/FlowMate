from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import MeetingSetupSession


def setup_now() -> datetime:
    return datetime.now(UTC)


async def get_open_setup(
    session: AsyncSession, user_id: UUID, *, now: datetime | None = None
) -> MeetingSetupSession | None:
    timestamp = now or setup_now()
    setup = await session.scalar(
        select(MeetingSetupSession).where(
            MeetingSetupSession.user_id == user_id,
            MeetingSetupSession.status == "open",
        )
    )
    if setup is not None and setup.expires_at <= timestamp:
        setup.status = "expired"
        await session.flush()
        return None
    return setup


async def open_setup(
    session: AsyncSession, user_id: UUID, *, ttl_minutes: int
) -> MeetingSetupSession:
    existing = await get_open_setup(session, user_id)
    if existing is not None:
        return existing
    now = setup_now()
    setup = MeetingSetupSession(
        user_id=user_id,
        status="open",
        step="type",
        context={"participant_ids": [], "topic_ids": [], "processed_update_ids": []},
        expires_at=now + timedelta(minutes=ttl_minutes),
    )
    session.add(setup)
    await session.flush()
    return setup


async def update_setup(
    session: AsyncSession,
    setup: MeetingSetupSession,
    *,
    step: str | None = None,
    values: dict[str, object] | None = None,
) -> None:
    context = dict(setup.context)
    if values:
        context.update(values)
    setup.context = context
    if step is not None:
        setup.step = step
    setup.updated_at = setup_now()
    await session.flush()


async def claim_setup_update(
    session: AsyncSession, setup: MeetingSetupSession, update_id: int
) -> bool:
    values = list(setup.context.get("processed_update_ids", []))
    if update_id in values:
        return False
    values.append(update_id)
    await update_setup(session, setup, values={"processed_update_ids": values[-50:]})
    return True


async def finish_setup(
    session: AsyncSession,
    setup: MeetingSetupSession,
    *,
    status: str,
    meeting_id: UUID | None = None,
) -> None:
    if status not in {"completed", "cancelled"}:
        raise ValueError("invalid setup status")
    setup.status = status
    setup.meeting_id = meeting_id
    setup.updated_at = setup_now()
    await session.flush()
