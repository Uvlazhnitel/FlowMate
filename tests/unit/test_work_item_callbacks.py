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
from flowmate.bot.handlers.work_items.callbacks import work_item_callback
from flowmate.bot.handlers.work_items.cards import encode_revision
from flowmate.bot.handlers.work_items.editing import start_input_session
from flowmate.db.models import Note, WorkItem, WorkItemEvent
from flowmate.reminders.preferences import NotificationDefaults
from flowmate.task_engine.details import WorkItemDetails
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.management import StaleWorkItemError


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
async def test_complete_callback_commits_and_refreshes_card() -> None:
    details = make_details()
    callback, update = make_callback(details.item)
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()
    preferences = SimpleNamespace(
        zoneinfo=ZoneInfo("UTC"),
        morning_digest_time=time(9),
        default_snooze_minutes=60,
    )
    events: list[str] = []

    async def acknowledge(*_: object, **__: object) -> None:
        events.append("acknowledged")

    async def complete(*_: object, **__: object) -> SimpleNamespace:
        events.append("completed")
        return SimpleNamespace(changed=True)

    with (
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=details.item.user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_work_item",
            new=AsyncMock(return_value=details.item),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_active_action_session",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_effective_notification_preferences",
            new=AsyncMock(return_value=preferences),
        ),
        patch(
            "flowmate.bot.handlers.work_items.lifecycle.complete_work_item",
            new=AsyncMock(side_effect=complete),
        ) as complete,
        patch(
            "flowmate.bot.handlers.work_items.cards.send_details",
            new=AsyncMock(return_value=True),
        ) as refresh,
        patch.object(
            CallbackQuery,
            "answer",
            new=AsyncMock(side_effect=acknowledge),
        ) as answer,
        patch.object(Message, "answer", new_callable=AsyncMock) as message_answer,
    ):
        await work_item_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
            notification_defaults(),
        )

    complete.assert_awaited_once()
    complete_call = complete.await_args
    assert complete_call is not None
    assert complete_call.kwargs["expected_revision"] == int(
        details.item.updated_at.timestamp() * 1_000_000
    )
    cast(AsyncMock, session.commit).assert_awaited_once()
    refresh.assert_awaited_once()
    refresh_call = refresh.await_args
    assert refresh_call is not None
    assert refresh_call.kwargs["edit"] is True
    assert refresh_call.kwargs["notice"] == f"✅ Выполнено: {details.item.title}"
    answer.assert_awaited_once_with("⏳ Выполняю…")
    assert events == ["acknowledged", "completed"]
    message_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_callback_only_refreshes_current_card() -> None:
    details = make_details()
    callback, update = make_callback(details.item)
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=details.item.user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_work_item",
            new=AsyncMock(return_value=details.item),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_active_action_session",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_effective_notification_preferences",
            new=AsyncMock(return_value=SimpleNamespace()),
        ),
        patch(
            "flowmate.bot.handlers.work_items.lifecycle.complete_work_item",
            new=AsyncMock(side_effect=StaleWorkItemError("stale")),
        ),
        patch(
            "flowmate.bot.handlers.work_items.cards.send_details",
            new=AsyncMock(return_value=True),
        ) as refresh,
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
        patch.object(Message, "answer", new_callable=AsyncMock) as message_answer,
    ):
        await work_item_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
            notification_defaults(),
        )

    cast(AsyncMock, session.commit).assert_not_awaited()
    cast(AsyncMock, session.rollback).assert_awaited_once()
    refresh.assert_awaited_once()
    refresh_call = refresh.await_args
    assert refresh_call is not None
    assert refresh_call.kwargs["notice"].startswith("⚠️ Карточка обновлена.")
    answer.assert_awaited_once_with("⏳ Выполняю…")
    message_answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_draft_blocks_inline_mutation() -> None:
    details = make_details()
    callback, update = make_callback(details.item)
    session = MagicMock(spec=AsyncSession)
    with (
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=details.item.user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_work_item",
            new=AsyncMock(return_value=details.item),
        ),
        patch(
            "flowmate.bot.handlers.work_items.callbacks.get_active_draft_for_user",
            new=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
        ),
        patch(
            "flowmate.bot.handlers.work_items.lifecycle.complete_work_item",
            new=AsyncMock(),
        ) as complete,
        patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer,
        patch.object(Message, "edit_text", new_callable=AsyncMock) as edit,
    ):
        await work_item_callback(
            callback,
            update,
            cast(AsyncSession, session),
            ZoneInfo("UTC"),
            30,
            notification_defaults(),
        )

    complete.assert_not_awaited()
    answer.assert_awaited_once_with("⏳ Выполняю…")
    edit_call = edit.await_args
    assert edit_call is not None
    assert edit_call.args[0].endswith(
        "⚠️ Сначала завершите или отмените активный черновик."
    )


@pytest.mark.asyncio
async def test_repeated_input_callback_does_not_send_second_force_reply() -> None:
    message = make_message("callback source")
    session = MagicMock(spec=AsyncSession)
    session.rollback = AsyncMock()
    action_session = SimpleNamespace(prompt_message_id=501)
    with (
        patch(
            "flowmate.bot.handlers.work_items.editing.create_action_session",
            new=AsyncMock(return_value=action_session),
        ) as create_session,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await start_input_session(
            message,
            cast(AsyncSession, session),
            user_id=uuid4(),
            item_id=uuid4(),
            action=WorkItemAction.ADD_NOTE,
            prompt="Введите текст заметки.",
            ttl_minutes=30,
            telegram_update_id=9003,
        )

    create_session.assert_awaited_once()
    cast(AsyncMock, session.rollback).assert_awaited_once()
    answer.assert_awaited_once_with("Запрос уже обработан.")
