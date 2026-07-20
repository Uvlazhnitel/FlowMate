from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import Note

NoteSource = Literal["text", "voice"]


def validate_note_input(content: str, telegram_update_id: int) -> str:
    normalized = content.strip()
    if not normalized:
        raise ValueError("Note content must not be blank")
    if telegram_update_id <= 0:
        raise ValueError("Telegram update ID must be positive")
    return normalized


async def get_note_by_telegram_update_id(
    session: AsyncSession,
    telegram_update_id: int,
) -> Note | None:
    if telegram_update_id <= 0:
        raise ValueError("Telegram update ID must be positive")
    statement = select(Note).where(Note.telegram_update_id == telegram_update_id)
    result = await session.scalars(statement)
    return result.one_or_none()


async def create_note_idempotently(
    session: AsyncSession,
    *,
    user_id: UUID,
    content: str,
    source: NoteSource,
    telegram_update_id: int,
) -> tuple[Note, bool]:
    normalized = validate_note_input(content, telegram_update_id)
    statement = (
        insert(Note)
        .values(
            id=uuid4(),
            user_id=user_id,
            content=normalized,
            source=source,
            telegram_update_id=telegram_update_id,
        )
        .on_conflict_do_nothing(constraint="notes_telegram_update_id_key")
        .returning(Note)
    )
    created_note = (await session.execute(statement)).scalar_one_or_none()
    if created_note is not None:
        await session.flush()
        return created_note, True

    existing_note = await get_note_by_telegram_update_id(
        session,
        telegram_update_id,
    )
    if existing_note is None:
        raise RuntimeError("Conflicting Telegram note could not be loaded")
    return existing_note, False


async def list_recent_notes_for_user(
    session: AsyncSession,
    user_id: UUID,
    *,
    limit: int = 10,
) -> list[Note]:
    if limit <= 0:
        raise ValueError("Note limit must be positive")
    statement = (
        select(Note)
        .where(Note.user_id == user_id)
        .order_by(Note.created_at.desc(), Note.id.desc())
        .limit(limit)
    )
    return list(await session.scalars(statement))
