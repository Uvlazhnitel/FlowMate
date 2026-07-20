from aiogram import Bot, Dispatcher

from flowmate.bot.handlers import create_router
from flowmate.core.config import Settings, get_settings
from flowmate.core.logging import configure_logging


def create_dispatcher(settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(settings.telegram_allowed_user_ids))
    return dispatcher


async def run_bot(settings: Settings | None = None) -> None:
    app_settings = (settings or get_settings()).require_bot()
    configure_logging(app_settings.log_level)
    bot_token = app_settings.telegram_bot_token
    if bot_token is None:  # Kept explicit for static type narrowing.
        raise RuntimeError("Telegram bot token validation failed")

    dispatcher = create_dispatcher(app_settings)
    async with Bot(token=bot_token) as bot:
        await dispatcher.start_polling(bot)
