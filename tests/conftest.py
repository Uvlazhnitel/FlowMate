from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from os import environ, getenv

import pytest
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from flowmate.db.session import create_engine

TEST_DATABASE_URL = getenv(
    "FLOWMATE_TEST_DATABASE_URL",
    "postgresql+asyncpg://flowmate:flowmate@localhost:5433/flowmate_test",
)


@pytest.fixture
async def database_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as error:
        await engine.dispose()
        if "FLOWMATE_TEST_DATABASE_URL" in environ:
            raise
        pytest.skip(f"PostgreSQL test database is unavailable: {error}")

    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def started_app(app: object) -> AsyncIterator[None]:
    # FastAPI exposes the configured lifespan context through its router.
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        yield
