from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowmate.bot.filters import ActiveDraftFilter
from flowmate.bot.handlers.clarification import active_draft_message
from flowmate.bot.handlers.drafts import (
    DRAFT_CANCELLED_MESSAGE,
    DRAFT_NOT_FOUND_MESSAGE,
    draft_callback,
    show_draft,
)
from flowmate.bot.handlers.notes import notes_command, text_note
from flowmate.bot.handlers.voice import voice_message
from flowmate.bot.middleware import AllowedUserMiddleware, DatabaseSessionMiddleware
from flowmate.db.drafts import get_active_draft_for_user, transition_draft
from flowmate.db.health import database_is_ready
from flowmate.db.users import get_or_create_telegram_user, get_user_by_telegram_id


async def start_command(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return

    user, _ = await get_or_create_telegram_user(
        db_session,
        telegram_user.id,
        display_name=telegram_user.full_name[:255],
    )
    user.display_name = telegram_user.full_name[:255]
    user.is_active = True
    await db_session.flush()
    await message.answer("Добро пожаловать! FlowMate готов к работе.")


async def help_command(message: Message) -> None:
    await message.answer(
        "Доступные команды: /start, /help, /status, /notes. "
        "Черновики: /draft, /cancel. "
        "Отправьте текст или голосовое сообщение, чтобы сохранить заметку."
    )


async def status_command(message: Message, db_engine: AsyncEngine) -> None:
    if await database_is_ready(db_engine):
        await message.answer("Бот работает, база данных доступна.")
        return
    await message.answer("Сервис временно недоступен. Попробуйте позже.")


async def unsupported_message(message: Message) -> None:
    await message.answer("Отправьте текст, голосовое сообщение или используйте /help.")


async def draft_command(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    draft = (
        await get_active_draft_for_user(db_session, user.id)
        if user is not None
        else None
    )
    if draft is None:
        await message.answer(DRAFT_NOT_FOUND_MESSAGE)
        return
    if draft.status == "parsing" or draft.analysis_payload is None:
        await message.answer("Черновик ещё анализируется.")
        return
    await show_draft(message, draft, db_session)


async def cancel_command(message: Message, db_session: AsyncSession) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    user = await get_user_by_telegram_id(db_session, telegram_user.id)
    draft = (
        await get_active_draft_for_user(db_session, user.id)
        if user is not None
        else None
    )
    if draft is None:
        await message.answer(DRAFT_NOT_FOUND_MESSAGE)
        return
    await transition_draft(db_session, draft, "cancelled")
    await db_session.commit()
    await message.answer(DRAFT_CANCELLED_MESSAGE)


def create_router(
    allowed_user_ids: frozenset[int],
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
) -> Router:
    router = Router(name="flowmate")
    router.message.outer_middleware(AllowedUserMiddleware(allowed_user_ids))
    router.callback_query.outer_middleware(AllowedUserMiddleware(allowed_user_ids))
    router.message.middleware(DatabaseSessionMiddleware(session_factory, engine))
    router.callback_query.middleware(DatabaseSessionMiddleware(session_factory, engine))
    router.message.register(start_command, Command("start"))
    router.message.register(help_command, Command("help"))
    router.message.register(status_command, Command("status"))
    router.message.register(notes_command, Command("notes"))
    router.message.register(draft_command, Command("draft"))
    router.message.register(cancel_command, Command("cancel"))
    router.message.register(
        active_draft_message,
        ActiveDraftFilter(),
        F.text | F.voice,
    )
    router.message.register(voice_message, F.voice)
    router.message.register(text_note, F.text & ~F.text.startswith("/"))
    router.message.register(unsupported_message)
    router.callback_query.register(draft_callback, F.data.startswith("draft:"))
    return router
