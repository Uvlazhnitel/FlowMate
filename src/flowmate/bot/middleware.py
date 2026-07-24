import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowmate.db.session import session_scope
from flowmate.db.users import get_user_by_telegram_id
from flowmate.stabilization.idempotency import (
    claim_telegram_update,
    complete_telegram_update,
    fail_telegram_update,
)
from flowmate.workspaces import activate_workspace

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
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user is not None else None
        if user_id is not None and user_id in self.allowed_user_ids:
            return await handler(event, data)

        if user_id is not None:
            self.logger.warning("unauthorized_telegram_user user_id=%s", user_id)
        if isinstance(event, CallbackQuery):
            await event.answer(
                "У вас нет доступа к этому действию.",  # noqa: RUF001
                show_alert=True,
            )
        else:
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


class WorkspaceContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("db_session")
        telegram_user = (
            event.from_user if isinstance(event, Message | CallbackQuery) else None
        )
        if isinstance(session, AsyncSession) and telegram_user is not None:
            user = await get_user_by_telegram_id(session, telegram_user.id)
            if user is not None:
                activate_workspace(
                    session,
                    user_id=user.id,
                    workspace=user.active_workspace,
                )
                data["flowmate_user"] = user
        return await handler(event, data)


class PersistentUpdateMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        update = data.get("event_update")
        session = data.get("db_session")
        if not isinstance(update, Update) or not isinstance(session, AsyncSession):
            return await handler(event, data)
        telegram_user_id = (
            event.from_user.id
            if isinstance(event, Message | CallbackQuery)
            and event.from_user is not None
            else None
        )
        event_kind = "callback" if isinstance(event, CallbackQuery) else "message"
        claim = await claim_telegram_update(
            session,
            update_id=update.update_id,
            telegram_user_id=telegram_user_id,
            event_kind=event_kind,
        )
        await session.commit()
        if not claim.accepted:
            if isinstance(event, CallbackQuery):
                await event.answer(
                    "Действие уже обработано."
                    if claim.duplicate
                    else "Действие обрабатывается."
                )
            return None
        try:
            result = await handler(event, data)
        except BaseException:
            await session.rollback()
            await fail_telegram_update(
                session, update.update_id, error_code="handler_exception"
            )
            await session.commit()
            raise
        await complete_telegram_update(session, update.update_id)
        await session.commit()
        return result
