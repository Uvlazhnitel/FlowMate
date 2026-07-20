from datetime import UTC, datetime

import pytest
from aiogram.types import Chat, Message, User

from flowmate.bot.filters import AllowedUserFilter


def make_message(user_id: int) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
    )


@pytest.mark.asyncio
async def test_allows_configured_user() -> None:
    user_filter = AllowedUserFilter(frozenset({123}))

    assert await user_filter(make_message(123)) is True


@pytest.mark.asyncio
async def test_rejects_unknown_user() -> None:
    user_filter = AllowedUserFilter(frozenset({123}))

    assert await user_filter(make_message(456)) is False
