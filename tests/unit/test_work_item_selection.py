from datetime import UTC, datetime, time
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import (
    DraftItemType,
    ManagementAction,
    ManagementIntent,
)
from flowmate.bot.handlers.work_items.cards import encode_revision
from flowmate.bot.handlers.work_items.selection import work_item_selection_callback
from flowmate.db.models import Note, WorkItem, WorkItemEvent
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.task_engine.details import WorkItemDetails
from flowmate.task_engine.enums import WorkItemAction


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
async def test_selection_can_be_cancelled_and_repeated_callback_expires() -> None:
    message = make_message("selection")
    session_id = uuid4()
    callback = CallbackQuery(
        id="selection-callback",
        from_user=cast(User, message.from_user),
        chat_instance="test",
        message=message,
        data=f"wis:{session_id}:x",
    )
    update = Update(update_id=9200, callback_query=callback)
    user = SimpleNamespace(id=uuid4())
    action_session = SimpleNamespace(
        id=session_id,
        action=WorkItemAction.SELECT_RECORD,
    )
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.selection.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.work_items.selection.get_action_session_for_user",
            new=AsyncMock(side_effect=[action_session, None]),
        ),
        patch(
            "flowmate.bot.handlers.work_items.selection.finish_action_session",
            new=AsyncMock(),
        ) as finish,
        patch.object(Message, "edit_text", new_callable=AsyncMock) as edit,
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
    ):
        await work_item_selection_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
        )
        await work_item_selection_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
        )

    finish.assert_awaited_once_with(session, action_session, status="cancelled")
    assert edit.await_args_list[0].args == ("✅ Выбор отменён.",)
    assert edit.await_args_list[0].kwargs == {"parse_mode": None}
    assert edit.await_args_list[1].args[0].endswith("⚠️ Срок выбора истёк.")
    assert [call.args for call in answer.await_args_list] == [
        ("⏳ Выполняю…",),
        ("⏳ Выполняю…",),
    ]


@pytest.mark.asyncio
async def test_selection_preserves_and_applies_intended_action() -> None:
    message = make_message("selection")
    session_id = uuid4()
    item = make_details("follow_up").item
    intent = make_intent()
    callback = CallbackQuery(
        id="selection-action",
        from_user=cast(User, message.from_user),
        chat_instance="test",
        message=message,
        data=f"wis:{session_id}:0",
    )
    update = Update(update_id=9201, callback_query=callback)
    user = SimpleNamespace(id=item.user_id)
    action_session = SimpleNamespace(
        id=session_id,
        action=WorkItemAction.SELECT_RECORD,
        context={
            "candidate_ids": [str(item.id)],
            "intent": intent.model_dump(mode="json"),
        },
    )
    session = MagicMock(spec=AsyncSession)
    session.flush = AsyncMock()
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.selection.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ),
        patch(
            "flowmate.bot.handlers.work_items.selection.get_action_session_for_user",
            new=AsyncMock(return_value=action_session),
        ),
        patch(
            "flowmate.bot.handlers.work_items.selection.get_work_item",
            new=AsyncMock(return_value=item),
        ),
        patch(
            "flowmate.bot.handlers.work_items.selection.finish_action_session",
            new=AsyncMock(),
        ) as finish,
        patch(
            "flowmate.bot.handlers.work_items.selection.apply_management_intent",
            new=AsyncMock(),
        ) as apply_intent,
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
        patch.object(Message, "edit_text", new_callable=AsyncMock) as edit,
    ):
        await work_item_selection_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
        )

    finish.assert_awaited_once_with(session, action_session)
    apply_intent.assert_awaited_once()
    apply_call = apply_intent.await_args
    assert apply_call is not None
    assert apply_call.kwargs["item"] == item
    assert apply_call.kwargs["intent"] == intent
    answer.assert_awaited_once_with("⏳ Выполняю…")
    edit_call = edit.await_args
    assert edit_call is not None
    assert edit_call.args[0].endswith("✅ Запись выбрана.")
    assert edit_call.kwargs["reply_markup"] is None
