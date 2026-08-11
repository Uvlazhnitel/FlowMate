from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Chat, Message, Update, User
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.service import DraftParsingService
from flowmate.bot.handlers.notes import (
    MANAGEMENT_ALREADY_PROCESSED_MESSAGE,
    text_note,
)
from flowmate.db.models import (
    Note,
    WorkItemEvent,
)
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.action_sessions import (
    create_action_session,
)
from flowmate.task_engine.enums import WorkItemAction
from flowmate.task_engine.management import (
    add_work_item_note,
    update_work_item_content,
    work_item_revision,
)
from flowmate.task_engine.service import (
    create_work_item,
)


@pytest.mark.integration
async def test_repeated_force_reply_update_stops_before_ai_and_new_note(
    database_session: AsyncSession,
) -> None:
    telegram_user_id = 630_011
    update_id = 830_050
    user = await create_telegram_user(database_session, telegram_user_id)
    user_id = user.id
    item = await create_work_item(
        database_session, user_id, item_type="task", title="Document result"
    )
    await add_work_item_note(
        database_session,
        user_id,
        item.id,
        update_id,
        "Private reply contents",
    )
    await database_session.commit()
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=telegram_user_id, type="private"),
        from_user=User(
            id=telegram_user_id,
            is_bot=False,
            first_name="Test",
        ),
        text="Private reply contents",
    )
    update = Update(update_id=update_id, message=message)
    service = MagicMock(spec=DraftParsingService)
    service.parse_text = AsyncMock()
    note_count = await database_session.scalar(
        select(func.count(Note.id)).where(Note.user_id == user_id)
    )

    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await text_note(
            message,
            update,
            database_session,
            cast(DraftParsingService, service),
        )

    service.parse_text.assert_not_awaited()
    answer.assert_awaited_once_with(MANAGEMENT_ALREADY_PROCESSED_MESSAGE)
    assert (
        await database_session.scalar(
            select(func.count(Note.id)).where(Note.user_id == user_id)
        )
        == note_count
    )
    assert (
        await database_session.scalar(
            select(func.count(WorkItemEvent.id)).where(
                WorkItemEvent.telegram_update_id == update_id
            )
        )
        == 1
    )


@pytest.mark.integration
async def test_content_edit_is_owned_idempotent_and_event_safe(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_020)
    other = await create_telegram_user(database_session, 630_021)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Old title",
        description="Old description",
    )
    revision = work_item_revision(item.updated_at)

    result = await update_work_item_content(
        database_session,
        user.id,
        item.id,
        830_200,
        title="New title",
        description=None,
        update_title=True,
        update_description=True,
        expected_revision=revision,
    )
    duplicate = await update_work_item_content(
        database_session,
        user.id,
        item.id,
        830_200,
        title="Ignored duplicate",
        update_title=True,
    )

    assert result.work_item.title == "New title"
    assert result.work_item.description is None
    assert result.event.payload == {"fields": ["title", "description"]}
    assert "New title" not in str(result.event.payload)
    assert duplicate.event.id == result.event.id
    with pytest.raises(ValueError, match="work item not found"):
        await update_work_item_content(
            database_session,
            other.id,
            item.id,
            830_201,
            title="Cross-user edit",
            update_title=True,
        )


@pytest.mark.integration
async def test_edit_field_action_is_allowed_by_current_schema(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, 630_022)
    item = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Editable task",
    )

    action = await create_action_session(
        database_session,
        user.id,
        action=WorkItemAction.EDIT_FIELD,
        work_item_id=item.id,
        ttl_minutes=30,
        context={"edit_field": "title"},
        telegram_update_id=830_202,
    )
    await database_session.flush()

    assert action.action == WorkItemAction.EDIT_FIELD.value
