from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from flowmate.db.session import session_scope


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = (
        request.app.state.session_factory
    )
    return session_factory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = get_session_factory(request)
    async with session_scope(session_factory) as session:
        yield session
