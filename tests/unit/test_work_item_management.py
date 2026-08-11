# ruff: noqa: RUF001
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
from flowmate.bot.handlers.work_items.cards import (
    encode_revision,
    parse_work_item_callback,
)
from flowmate.bot.handlers.work_items.management import (
    apply_management_intent,
    execute_management_intent,
)
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
async def test_high_confidence_management_executes_single_match() -> None:
    message = make_message("закрой follow-up с Антоном")
    update = Update(update_id=9001, message=message)
    user_id = uuid4()
    item = WorkItem(
        id=uuid4(),
        user_id=user_id,
        type="follow_up",
        title="Связаться с Антоном",
        status="inbox",
        priority="normal",
    )
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.management.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.management.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.management.find_intent_targets",
            new=AsyncMock(return_value=[item]),
        ),
        patch(
            "flowmate.bot.handlers.work_items.management.complete_work_item",
            new=AsyncMock(),
        ) as complete,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        handled = await execute_management_intent(
            message,
            update,
            cast(AsyncSession, session),
            make_intent(),
            high_confidence_threshold=0.8,
            action_ttl_minutes=30,
            app_timezone=ZoneInfo("UTC"),
        )

    assert handled.value == "handled"
    complete.assert_awaited_once_with(
        cast(AsyncSession, session),
        user_id,
        item.id,
        telegram_update_id=9001,
    )
    cast(AsyncMock, session.commit).assert_awaited_once()
    answer.assert_awaited_once_with(f"✅ Выполнено: {item.title}")


@pytest.mark.asyncio
async def test_contextual_management_without_reply_never_guesses_target() -> None:
    message = make_message("нужно завтра выполнить эту задачу")
    update = Update(update_id=9003, message=message)
    user_id = uuid4()
    intent = make_intent().model_copy(
        update={
            "action": ManagementAction.RESCHEDULE,
            "contextual_reference": True,
            "record_query": "задача",
        }
    )
    session = MagicMock(spec=AsyncSession)
    find_targets = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.management.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.management.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.management.find_intent_targets",
            new=find_targets,
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        outcome = await execute_management_intent(
            message,
            update,
            cast(AsyncSession, session),
            intent,
            high_confidence_threshold=0.8,
            action_ttl_minutes=30,
            app_timezone=ZoneInfo("UTC"),
        )

    assert outcome.value == "ambiguous"
    find_targets.assert_not_awaited()
    answer.assert_awaited_once_with(
        "Ответьте Reply на карточку нужной записи или укажите её название."
    )


@pytest.mark.asyncio
async def test_ambiguous_management_creates_selection_without_mutation() -> None:
    message = make_message("закрой follow-up с Антоном")
    update = Update(update_id=9002, message=message)
    user_id = uuid4()
    items = [
        WorkItem(
            id=uuid4(),
            user_id=user_id,
            type="follow_up",
            title=f"Follow-up {index}",
            status="inbox",
            priority="normal",
        )
        for index in range(2)
    ]
    action_session = SimpleNamespace(id=uuid4())
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.management.get_user_by_telegram_id",
            new=AsyncMock(return_value=SimpleNamespace(id=user_id)),
        ),
        patch(
            "flowmate.bot.handlers.work_items.management.get_active_draft_for_user",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "flowmate.bot.handlers.work_items.management.find_intent_targets",
            new=AsyncMock(return_value=items),
        ),
        patch(
            "flowmate.bot.handlers.work_items.management.create_action_session",
            new=AsyncMock(return_value=action_session),
        ) as create_session,
        patch(
            "flowmate.bot.handlers.work_items.management.complete_work_item",
            new=AsyncMock(),
        ) as complete,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await execute_management_intent(
            message,
            update,
            cast(AsyncSession, session),
            make_intent(),
            high_confidence_threshold=0.8,
            action_ttl_minutes=30,
            app_timezone=ZoneInfo("UTC"),
        )

    create_session.assert_awaited_once()
    complete.assert_not_awaited()
    cast(AsyncMock, session.commit).assert_awaited_once()
    answer_call = answer.await_args
    assert answer_call is not None
    assert answer_call.args[0].startswith("Выберите запись:\n\n")
    assert "🔁 Follow-up 0" in answer_call.args[0]
    assert "Люди:" not in answer_call.args[0]
    assert len(answer_call.kwargs["reply_markup"].inline_keyboard) == 3
    assert answer_call.kwargs["reply_markup"].inline_keyboard[-1][0].text == "Отмена"


@pytest.mark.asyncio
async def test_waiting_received_offer_uses_revision_aware_follow_up_callback() -> None:
    message = make_message("Антон ответил")
    update = Update(update_id=9202, message=message)
    item = make_details("waiting").item
    intent = ManagementIntent(
        action=ManagementAction.WAITING_RECEIVED,
        target_type=DraftItemType.WAITING,
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
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    with (
        patch(
            "flowmate.bot.handlers.work_items.management.mark_waiting_received",
            new=AsyncMock(),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await apply_management_intent(
            message,
            update,
            cast(AsyncSession, session),
            user_id=item.user_id,
            telegram_user_id=123,
            item=item,
            intent=intent,
            action_ttl_minutes=30,
            app_timezone=ZoneInfo("UTC"),
        )

    assert answer.await_count == 2
    follow_up_markup = answer.await_args_list[1].kwargs["reply_markup"]
    callback_data = follow_up_markup.inline_keyboard[0][0].callback_data
    parsed = parse_work_item_callback(callback_data)
    assert parsed is not None
    assert parsed[0] == "f"
    assert parsed[1] == item.id
    assert parsed[2] == encode_revision(int(item.updated_at.timestamp() * 1_000_000))
    session.refresh.assert_awaited_once_with(item, attribute_names=["updated_at"])
