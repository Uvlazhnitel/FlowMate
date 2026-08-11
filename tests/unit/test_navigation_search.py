from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import Chat, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import SearchIntent, SearchWorkItemType
from flowmate.bot.handlers.commands import cancel_command
from flowmate.bot.handlers.navigation.presentation import parse_search_callback
from flowmate.bot.handlers.navigation.search import (
    execute_search_intent,
    parse_search_expression,
)
from flowmate.db.models import WorkItem


def make_message() -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=100, type="private"),
        from_user=User(id=100, is_bot=False, first_name="Test"),
        text="/menu",
    )


def test_search_callback_uses_only_session_id_and_page() -> None:
    session_id = uuid4()

    assert parse_search_callback(f"lq:{session_id}:2") == (session_id, 2)
    assert parse_search_callback(f"lq:{session_id}:-1") is None
    assert parse_search_callback("lq:not-a-uuid:0") is None


def test_search_expression_parses_filters_and_quoted_values() -> None:
    filters = parse_search_expression(
        'release person:"Антон Иванов" topic:Testing type:follow-up '
        "status:active,waiting from:2026-07-01 to:2026-07-31",
        ZoneInfo("Europe/Riga"),
    )

    assert filters.text_query == "release"
    assert filters.person_query == "Антон Иванов"
    assert filters.topic_query == "Testing"
    assert filters.item_types == ("follow_up",)
    assert filters.statuses == ("active", "waiting")
    assert filters.due_from is not None and filters.due_from.hour == 0
    assert (
        filters.due_to is not None and filters.due_to.date().isoformat() == "2026-08-01"
    )


@pytest.mark.parametrize(
    "query",
    [
        "type:unknown",
        "status:all status:done",
        "from:31.07.2026",
        "from:2026-08-01 to:2026-07-01",
        "overdue from:2026-07-01",
        'person:"unfinished',
    ],
)
def test_search_expression_rejects_invalid_filters(query: str) -> None:
    with pytest.raises(ValueError):
        parse_search_expression(query, ZoneInfo("UTC"))


@pytest.mark.asyncio
async def test_cancel_command_cancels_active_search_action() -> None:
    message = make_message()
    session = AsyncMock(spec=AsyncSession)
    user = SimpleNamespace(id=uuid4())
    with (
        patch(
            "flowmate.bot.handlers.commands.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.commands.cancel_transient_dialogs",
            new=AsyncMock(return_value=SimpleNamespace(total=1)),
        ) as cancel_all,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await cancel_command(message, cast(AsyncSession, session))

    cancel_all.assert_awaited_once_with(session, user.id)
    session.commit.assert_awaited_once()
    answer.assert_awaited_once()
    call = answer.await_args
    assert call is not None
    assert call.args == ("Текущее действие отменено",)
    assert call.kwargs["reply_markup"].is_persistent is True


def make_search_intent(*, confidence: float = 0.95) -> SearchIntent:
    return SearchIntent(
        text_query=None,
        person_query="Антон",
        topic_query=None,
        item_types=[SearchWorkItemType.FOLLOW_UP],
        statuses=[],
        include_all_statuses=False,
        due_from=None,
        due_to=None,
        overdue=False,
        stale_contacts=False,
        ambiguities=[],
        confidence=confidence,
    )


@pytest.mark.asyncio
async def test_conversational_search_opens_one_clear_result() -> None:
    message = make_message()
    update = Update(update_id=9701, message=message)
    session = AsyncMock(spec=AsyncSession)
    user = SimpleNamespace(id=uuid4())
    action = SimpleNamespace(id=uuid4(), context={}, status="completed")
    item = WorkItem(
        id=uuid4(),
        user_id=user.id,
        type="follow_up",
        title="Позвонить Антону",
        status="inbox",
        priority="normal",
    )
    with (
        patch(
            "flowmate.bot.handlers.navigation.search.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.navigation.search.create_action_session",
            new=AsyncMock(return_value=action),
        ),
        patch(
            "flowmate.bot.handlers.navigation.search.finish_action_session",
            new=AsyncMock(),
        ),
        patch(
            "flowmate.bot.handlers.navigation.search.search_work_items",
            new=AsyncMock(return_value=[item]),
        ),
        patch(
            "flowmate.bot.handlers.navigation.search.send_details",
            new=AsyncMock(return_value=True),
        ) as send_details,
    ):
        await execute_search_intent(
            message,
            update,
            cast(AsyncSession, session),
            make_search_intent(),
            high_confidence_threshold=0.8,
            action_ttl_minutes=30,
            timezone=ZoneInfo("UTC"),
        )

    session.commit.assert_awaited_once()
    send_details.assert_awaited_once_with(
        message,
        session,
        user.id,
        item,
        ZoneInfo("UTC"),
    )


@pytest.mark.asyncio
async def test_low_confidence_search_does_not_query_database() -> None:
    message = make_message()
    with (
        patch(
            "flowmate.bot.handlers.navigation.search.search_work_items",
            new=AsyncMock(),
        ) as search,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await execute_search_intent(
            message,
            Update(update_id=9702, message=message),
            cast(AsyncSession, AsyncMock(spec=AsyncSession)),
            make_search_intent(confidence=0.4),
            high_confidence_threshold=0.8,
            action_ttl_minutes=30,
            timezone=ZoneInfo("UTC"),
        )

    search.assert_not_awaited()
    answer.assert_awaited_once_with("Уточните поисковый запрос и попробуйте ещё раз.")
