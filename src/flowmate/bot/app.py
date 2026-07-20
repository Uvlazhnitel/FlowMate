import logging

from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowmate.bot.handlers import create_router
from flowmate.core.config import Settings, get_settings
from flowmate.core.logging import configure_logging
from flowmate.db.session import create_engine, create_session_factory


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    engine: AsyncEngine,
) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_router(settings.telegram_allowed_user_ids, session_factory, engine)
    )
    return dispatcher


async def run_bot(settings: Settings | None = None) -> None:
    app_settings = (settings or get_settings()).require_bot()
    configure_logging(
        app_settings.log_level,
        structured=app_settings.app_env == "production",
    )
    bot_token_secret = app_settings.telegram_bot_token
    if bot_token_secret is None:  # Kept explicit for static type narrowing.
        raise RuntimeError("Telegram bot token validation failed")
    bot_token = bot_token_secret.get_secret_value()

    engine = create_engine(app_settings.database_url)
    session_factory = create_session_factory(engine)
    dispatcher = create_dispatcher(app_settings, session_factory, engine)
    logger = logging.getLogger(__name__)
    try:
        async with Bot(token=bot_token) as bot:
            logger.info("telegram_bot_started")
            await dispatcher.start_polling(
                bot,
                allowed_updates=dispatcher.resolve_used_update_types(),
                close_bot_session=False,
            )
    finally:
        await engine.dispose()
        logger.info("telegram_bot_stopped")
