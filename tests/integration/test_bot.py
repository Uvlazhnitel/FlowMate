from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiogram.types import Chat, Message
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.handlers.commands import start_command
from flowmate.db.models import User


def make_start_message(user_id: int, first_name: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=TelegramUser(
            id=user_id,
            is_bot=False,
            first_name=first_name,
        ),
        text="/start",
    )


@pytest.mark.integration
async def test_repeated_start_updates_one_user(
    database_session: AsyncSession,
) -> None:
    first_message = make_start_message(200_001, "Первое имя")
    second_message = make_start_message(200_001, "Новое имя")

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await start_command(first_message, database_session)
        first_user = await database_session.scalar(
            select(User).where(User.telegram_user_id == 200_001)
        )
        assert first_user is not None
        first_id = first_user.id
        first_user.is_active = False
        await database_session.flush()

        await start_command(second_message, database_session)

    users_count = await database_session.scalar(
        select(func.count(User.id)).where(User.telegram_user_id == 200_001)
    )
    updated_user = await database_session.scalar(
        select(User).where(User.telegram_user_id == 200_001)
    )

    assert answer.await_count == 2
    answer.assert_awaited_with("Добро пожаловать! FlowMate готов к работе.")
    assert users_count == 1
    assert updated_user is not None
    assert updated_user.id == first_id
    assert updated_user.display_name == "Новое имя"
    assert updated_user.is_active is True
