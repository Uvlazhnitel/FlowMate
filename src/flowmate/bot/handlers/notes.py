# ruff: noqa: RUF001
import logging
from typing import Literal

from aiogram.types import Message, Update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import Note
from flowmate.db.notes import (
    NoteSource,
    create_note_idempotently,
    list_recent_notes_for_user,
)
from flowmate.db.users import get_or_create_telegram_user, get_user_by_telegram_id

NOTE_SAVED_MESSAGE = "Заметка сохранена."
NOTE_ALREADY_SAVED_MESSAGE = "Заметка уже сохранена."
NOTE_EMPTY_MESSAGE = "Заметка не может быть пустой."
NOTE_SAVE_FAILED_MESSAGE = "Не удалось сохранить заметку. Попробуйте позже."
NOTE_LIST_FAILED_MESSAGE = "Не удалось загрузить заметки. Попробуйте позже."
NO_NOTES_MESSAGE = "Заметок пока нет."
NOTE_PREVIEW_LENGTH = 300
NOTE_LIST_LIMIT = 10

NoteSaveResult = Literal["created", "duplicate", "failed"]

logger = logging.getLogger(__name__)


async def save_note_for_message(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
    *,
    content: str,
    source: NoteSource,
) -> NoteSaveResult:
    telegram_user = message.from_user
    if telegram_user is None:
        return "failed"

    try:
        user, _ = await get_or_create_telegram_user(
            db_session,
            telegram_user.id,
            display_name=telegram_user.full_name[:255],
        )
        _, created = await create_note_idempotently(
            db_session,
            user_id=user.id,
            content=content,
            source=source,
            telegram_update_id=event_update.update_id,
        )
        await db_session.commit()
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_note_database_failed user_id=%s operation=create",
            telegram_user.id,
        )
        return "failed"

    return "created" if created else "duplicate"


async def text_note(
    message: Message,
    event_update: Update,
    db_session: AsyncSession,
) -> None:
    content = message.text.strip() if message.text is not None else ""
    if not content:
        await message.answer(NOTE_EMPTY_MESSAGE)
        return

    result = await save_note_for_message(
        message,
        event_update,
        db_session,
        content=content,
        source="text",
    )
    if result == "failed":
        await message.answer(NOTE_SAVE_FAILED_MESSAGE)
    elif result == "duplicate":
        await message.answer(NOTE_ALREADY_SAVED_MESSAGE)
    else:
        await message.answer(NOTE_SAVED_MESSAGE)


def format_note_preview(note: Note, position: int) -> str:
    normalized = " ".join(note.content.split())
    if len(normalized) > NOTE_PREVIEW_LENGTH:
        normalized = f"{normalized[: NOTE_PREVIEW_LENGTH - 3]}..."
    source = "голос" if note.source == "voice" else "текст"
    created_at = note.created_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"{position}. [{source}] {created_at}\n{normalized}"


async def notes_command(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return

    try:
        user = await get_user_by_telegram_id(db_session, telegram_user.id)
        notes = (
            await list_recent_notes_for_user(
                db_session,
                user.id,
                limit=NOTE_LIST_LIMIT,
            )
            if user is not None
            else []
        )
        await db_session.rollback()
    except SQLAlchemyError:
        await db_session.rollback()
        logger.error(
            "telegram_note_database_failed user_id=%s operation=list",
            telegram_user.id,
        )
        await message.answer(NOTE_LIST_FAILED_MESSAGE)
        return

    if not notes:
        await message.answer(NO_NOTES_MESSAGE)
        return

    response = "Последние заметки:\n\n" + "\n\n".join(
        format_note_preview(note, position)
        for position, note in enumerate(notes, start=1)
    )
    await message.answer(response, parse_mode=None)
