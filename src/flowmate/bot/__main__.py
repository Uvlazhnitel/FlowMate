import asyncio
import logging

from aiogram import Bot, Dispatcher

from flowmate.bot.handlers import create_router
from flowmate.config import get_settings


async def main() -> None:
    settings = get_settings().require_bot()
    logging.basicConfig(level=settings.log_level)
    bot_token = settings.telegram_bot_token
    if bot_token is None:  # Kept explicit for static type narrowing.
        raise RuntimeError("Telegram bot token validation failed")

    dispatcher = Dispatcher()
    dispatcher.include_router(create_router(settings.telegram_allowed_user_ids))

    async with Bot(token=bot_token) as bot:
        await dispatcher.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
