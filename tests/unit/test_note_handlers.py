import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from aiogram.types import Chat, Message, Update, User
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.bot.handlers.notes import (
    NO_NOTES_MESSAGE,
    NOTE_ALREADY_SAVED_MESSAGE,
    NOTE_EMPTY_MESSAGE,
    NOTE_LIST_FAILED_MESSAGE,
    NOTE_SAVED_MESSAGE,
    format_note_preview,
    notes_command,
    save_note_for_message,
    text_note,
)
from flowmate.db.models import Note


def make_message(user_id: int = 123, text: str = "Заметка") -> Message:
    return Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="Test"),
        text=text,
    )


def make_update(message: Message, update_id: int = 1001) -> Update:
    return Update(update_id=update_id, message=message)


def make_session() -> AsyncSession:
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return cast(AsyncSession, session)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("created", "expected_result"),
    [(True, "created"), (False, "duplicate")],
)
async def test_save_note_commits_created_or_duplicate_result(
    created: bool,
    expected_result: str,
) -> None:
    message = make_message()
    update = make_update(message)
    session = make_session()
    user = SimpleNamespace(id=uuid4())
    with (
        patch(
            "flowmate.bot.handlers.notes.get_or_create_telegram_user",
            new=AsyncMock(return_value=(user, True)),
        ),
        patch(
            "flowmate.bot.handlers.notes.create_note_idempotently",
            new=AsyncMock(return_value=(object(), created)),
        ) as create_note,
    ):
        result = await save_note_for_message(
            message,
            update,
            session,
            content="  Content  ",
            source="text",
        )

    assert result == expected_result
    create_note.assert_awaited_once_with(
        session,
        user_id=user.id,
        content="  Content  ",
        source="text",
        telegram_update_id=1001,
    )
    cast(AsyncMock, session.commit).assert_awaited_once()
    cast(AsyncMock, session.rollback).assert_not_awaited()


@pytest.mark.asyncio
async def test_save_note_rolls_back_and_logs_no_content_on_database_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    message = make_message(text="private note contents")
    session = make_session()
    with (
        patch(
            "flowmate.bot.handlers.notes.get_or_create_telegram_user",
            new=AsyncMock(side_effect=SQLAlchemyError("private database detail")),
        ),
        caplog.at_level(logging.ERROR, logger="flowmate.bot.handlers.notes"),
    ):
        result = await save_note_for_message(
            message,
            make_update(message),
            session,
            content=message.text or "",
            source="text",
        )

    assert result == "failed"
    cast(AsyncMock, session.rollback).assert_awaited_once()
    assert "user_id=123" in caplog.text
    assert "private note contents" not in caplog.text
    assert "private database detail" not in caplog.text


@pytest.mark.asyncio
async def test_text_note_rejects_blank_content() -> None:
    message = make_message(text="   ")
    with patch.object(Message, "answer", new_callable=AsyncMock) as answer:
        await text_note(message, make_update(message), make_session())

    answer.assert_awaited_once_with(NOTE_EMPTY_MESSAGE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected"),
    [("created", NOTE_SAVED_MESSAGE), ("duplicate", NOTE_ALREADY_SAVED_MESSAGE)],
)
async def test_text_note_returns_safe_save_status(result: str, expected: str) -> None:
    message = make_message(text="  Content  ")
    with (
        patch(
            "flowmate.bot.handlers.notes.save_note_for_message",
            new=AsyncMock(return_value=result),
        ) as save_note,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await text_note(message, make_update(message), make_session())

    save_note.assert_awaited_once()
    answer.assert_awaited_once_with(expected)


def test_note_preview_is_plain_compact_and_bounded() -> None:
    note = Note(
        user_id=uuid4(),
        content="line one\n" + "x" * 400,
        source="voice",
        telegram_update_id=1001,
        created_at=datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
    )

    preview = format_note_preview(note, 1)

    assert preview.startswith("1. [голос] 2026-07-20 12:30 UTC\nline one ")
    assert len(preview.split("\n", maxsplit=1)[1]) == 300
    assert preview.endswith("...")


@pytest.mark.asyncio
async def test_notes_command_lists_only_requested_users_notes_as_plain_text() -> None:
    message = make_message(text="/notes")
    session = make_session()
    user = SimpleNamespace(id=uuid4())
    note = Note(
        user_id=user.id,
        content="<b>not markup</b>",
        source="text",
        telegram_update_id=1001,
        created_at=datetime(2026, 7, 20, 12, 30, tzinfo=UTC),
    )
    with (
        patch(
            "flowmate.bot.handlers.notes.get_user_by_telegram_id",
            new=AsyncMock(return_value=user),
        ) as get_user,
        patch(
            "flowmate.bot.handlers.notes.list_recent_notes_for_user",
            new=AsyncMock(return_value=[note]),
        ) as list_notes,
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await notes_command(message, session)

    get_user.assert_awaited_once_with(session, 123)
    list_notes.assert_awaited_once_with(session, user.id, limit=10)
    cast(AsyncMock, session.rollback).assert_awaited_once()
    answer.assert_awaited_once_with(
        "Последние заметки:\n\n1. [текст] 2026-07-20 12:30 UTC\n<b>not markup</b>",
        parse_mode=None,
    )


@pytest.mark.asyncio
async def test_notes_command_handles_empty_list_and_database_failure() -> None:
    message = make_message(text="/notes")
    session = make_session()
    with (
        patch(
            "flowmate.bot.handlers.notes.get_user_by_telegram_id",
            new=AsyncMock(return_value=None),
        ),
        patch.object(Message, "answer", new_callable=AsyncMock) as answer,
    ):
        await notes_command(message, session)

    answer.assert_awaited_once_with(NO_NOTES_MESSAGE)

    answer.reset_mock()
    with (
        patch(
            "flowmate.bot.handlers.notes.get_user_by_telegram_id",
            new=AsyncMock(side_effect=SQLAlchemyError("private")),
        ),
        patch.object(Message, "answer", new=answer),
    ):
        await notes_command(message, session)

    answer.assert_awaited_once_with(NOTE_LIST_FAILED_MESSAGE)
