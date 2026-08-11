# ruff: noqa: RUF001
from datetime import UTC, datetime, time
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import (
    DraftItemType,
    ManagementAction,
    ManagementIntent,
)
from flowmate.bot.handlers.navigation.lists import today_command
from flowmate.bot.handlers.work_items.cards import encode_revision
from flowmate.db.models import Note, WorkItem, WorkItemEvent
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.task_engine.details import WorkItemDetails


def make_message(text: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=123, type="private"),
        from_user=User(id=123, is_bot=False, first_name="Test"),
        text=text,
    )


def make_intent() -> ManagementIntent:
    return ManagementIntent(
        action=ManagementAction.COMPLETE,
        target_type=DraftItemType.FOLLOW_UP,
        record_query="Антон",
        contextual_reference=False,
        person_candidate="Антон",
        topic_candidate=None,
        note_text=None,
        temporal_candidate=None,
        missing_fields=[],
        ambiguities=[],
        confidence=0.95,
    )


def make_details(item_type: str = "task", status: str = "inbox") -> WorkItemDetails:
    now = datetime(2026, 7, 22, 9, tzinfo=UTC)
    user_id = uuid4()
    item = WorkItem(
        id=uuid4(),
        user_id=user_id,
        type=item_type,
        title="  Important\nwork  ",
        description="Description " * 200,
        status=status,
        priority="normal",
        updated_at=now,
    )
    note = Note(
        id=uuid4(),
        user_id=user_id,
        content="Private linked note",
        source="manual",
        created_at=now,
    )
    event = WorkItemEvent(
        id=uuid4(),
        user_id=user_id,
        work_item_id=item.id,
        event_type="created",
        payload={},
        created_at=now,
    )
    return WorkItemDetails(
        item=item,
        topic_name="Testing",
        person_names=("Антон",),
        notes=(note,),
        events=(event,),
        nearest_reminder=None,
    )


def notification_defaults() -> NotificationDefaults:
    return NotificationDefaults(
        timezone="UTC",
        morning_digest_time=time(9),
        evening_digest_time=time(18),
        quiet_hours_start=time(22),
        quiet_hours_end=time(8),
        snooze_minutes=60,
    )


def make_callback(item: WorkItem, action: str = "c") -> tuple[CallbackQuery, Update]:
    message = make_message("card")
    revision = encode_revision(int(item.updated_at.timestamp() * 1_000_000))
    callback = CallbackQuery(
        id="callback-id",
        from_user=cast(User, message.from_user),
        chat_instance="test",
        message=message,
        data=f"wi:{action}:{item.id}:{revision}",
    )
    return callback, Update(update_id=9100, callback_query=callback)


@pytest.mark.asyncio
async def test_list_command_returns_safe_database_error() -> None:
    message = make_message("/today")
    session = MagicMock(spec=AsyncSession)
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.navigation.lists.get_user_by_telegram_id",
            new=AsyncMock(side_effect=SQLAlchemyError("private database detail")),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await today_command(message, cast(AsyncSession, session), ZoneInfo("UTC"))

    cast(AsyncMock, session.rollback).assert_awaited_once()
    answer.assert_awaited_once_with("Не удалось загрузить список. Попробуйте позже.")
