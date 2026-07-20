import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import User
from flowmate.db.users import (
    create_telegram_user,
    get_or_create_telegram_user,
    get_user_by_telegram_id,
)


@pytest.mark.integration
async def test_user_can_be_inserted_and_read(database_session: AsyncSession) -> None:
    created = await create_telegram_user(
        database_session, 100_001, display_name="Test User"
    )
    loaded = await get_user_by_telegram_id(database_session, 100_001)

    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.display_name == "Test User"
    assert loaded.is_active is True
    assert loaded.created_at.tzinfo is not None
    assert loaded.updated_at.tzinfo is not None


@pytest.mark.integration
async def test_user_can_exist_without_telegram_id(
    database_session: AsyncSession,
) -> None:
    user = User(display_name="Future Channel User")
    database_session.add(user)
    await database_session.flush()

    assert user.telegram_user_id is None


@pytest.mark.integration
async def test_telegram_user_id_is_unique(database_session: AsyncSession) -> None:
    await create_telegram_user(database_session, 100_002)
    duplicate = User(telegram_user_id=100_002)
    database_session.add(duplicate)

    with pytest.raises(IntegrityError):
        await database_session.flush()


@pytest.mark.integration
async def test_get_or_create_does_not_create_duplicates(
    database_session: AsyncSession,
) -> None:
    first, first_created = await get_or_create_telegram_user(
        database_session, 100_003, display_name="Original Name"
    )
    second, second_created = await get_or_create_telegram_user(
        database_session, 100_003, display_name="Changed Name"
    )
    count = await database_session.scalar(
        select(func.count(User.id)).where(User.telegram_user_id == 100_003)
    )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert second.display_name == "Original Name"
    assert count == 1


@pytest.mark.integration
@pytest.mark.parametrize("telegram_user_id", [0, -1])
async def test_repository_rejects_invalid_telegram_user_id(
    database_session: AsyncSession, telegram_user_id: int
) -> None:
    with pytest.raises(ValueError, match="positive"):
        await create_telegram_user(database_session, telegram_user_id)
