import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowmate.db.session import session_scope

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class AllowedUserMiddleware(BaseMiddleware):
    def __init__(self, allowed_user_ids: frozenset[int]) -> None:
        self.allowed_user_ids = allowed_user_ids
        self.logger = logging.getLogger(__name__)

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user is not None else None
        if user_id is not None and user_id in self.allowed_user_ids:
            return await handler(event, data)

        if user_id is not None:
            self.logger.warning("unauthorized_telegram_user user_id=%s", user_id)
        await event.answer("У вас нет доступа к этому боту.")  # noqa: RUF001
        return None


class DatabaseSessionMiddleware(BaseMiddleware):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: AsyncEngine,
    ) -> None:
        self.session_factory = session_factory
        self.engine = engine

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with session_scope(self.session_factory) as session:
            data["db_session"] = session
            data["db_engine"] = self.engine
            return await handler(event, data)
