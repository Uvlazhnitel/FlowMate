from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowmate.bot.handlers.notes import notes_command, text_note
from flowmate.bot.handlers.voice import voice_message
from flowmate.bot.middleware import AllowedUserMiddleware, DatabaseSessionMiddleware
from flowmate.db.health import database_is_ready
from flowmate.db.users import get_or_create_telegram_user


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
        "Отправьте текст или голосовое сообщение, чтобы сохранить заметку."
    )


async def status_command(message: Message, db_engine: AsyncEngine) -> None:
    if await database_is_ready(db_engine):
        await message.answer("Бот работает, база данных доступна.")
        return
    await message.answer("Сервис временно недоступен. Попробуйте позже.")


async def unsupported_message(message: Message) -> None:
    await message.answer("Отправьте текст, голосовое сообщение или используйте /help.")


def create_router(
    allowed_user_ids: frozenset[int],
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
) -> Router:
    router = Router(name="flowmate")
    router.message.outer_middleware(AllowedUserMiddleware(allowed_user_ids))
    router.message.middleware(DatabaseSessionMiddleware(session_factory, engine))
    router.message.register(start_command, Command("start"))
    router.message.register(help_command, Command("help"))
    router.message.register(status_command, Command("status"))
    router.message.register(notes_command, Command("notes"))
    router.message.register(voice_message, F.voice)
    router.message.register(text_note, F.text & ~F.text.startswith("/"))
    router.message.register(unsupported_message)
    return router
