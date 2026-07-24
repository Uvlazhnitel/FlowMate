from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import User
from flowmate.workspaces import Workspace, normalize_workspace


def validate_telegram_user_id(telegram_user_id: int) -> None:
    if telegram_user_id <= 0:
        raise ValueError("Telegram user ID must be positive")


async def get_user_by_telegram_id(
    session: AsyncSession, telegram_user_id: int
) -> User | None:
    validate_telegram_user_id(telegram_user_id)
    statement = select(User).where(User.telegram_user_id == telegram_user_id)
    result = await session.scalars(statement)
    return result.one_or_none()


async def create_telegram_user(
    session: AsyncSession,
    telegram_user_id: int,
    display_name: str | None = None,
    active_workspace: Workspace | str = Workspace.PERSONAL,
) -> User:
    validate_telegram_user_id(telegram_user_id)
    user = User(
        telegram_user_id=telegram_user_id,
        display_name=display_name,
        active_workspace=normalize_workspace(active_workspace),
    )
    session.add(user)
    await session.flush()
    return user


async def get_or_create_telegram_user(
    session: AsyncSession,
    telegram_user_id: int,
    display_name: str | None = None,
    active_workspace: Workspace | str = Workspace.PERSONAL,
) -> tuple[User, bool]:
    validate_telegram_user_id(telegram_user_id)
    statement = (
        insert(User)
        .values(
            id=uuid4(),
            telegram_user_id=telegram_user_id,
            display_name=display_name,
            is_active=True,
            active_workspace=normalize_workspace(active_workspace),
        )
        .on_conflict_do_nothing(constraint="users_telegram_user_id_key")
        .returning(User)
    )
    result = await session.execute(statement)
    created_user = result.scalar_one_or_none()
    if created_user is not None:
        await session.flush()
        return created_user, True

    existing_user = await get_user_by_telegram_id(session, telegram_user_id)
    if existing_user is None:
        raise RuntimeError("Conflicting Telegram user could not be loaded")
    return existing_user, False
