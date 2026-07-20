from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from os import environ, getenv

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from flowmate.core.config import get_settings
from flowmate.db.session import create_engine

TEST_DATABASE_URL = getenv(
    "TEST_DATABASE_URL",
    getenv(
        "FLOWMATE_TEST_DATABASE_URL",
        "postgresql+asyncpg://flowmate_test:flowmate_test@localhost:5433/flowmate_test",
    ),
)


def handle_database_unavailable(error: Exception) -> None:
    if "TEST_DATABASE_URL" in environ or "FLOWMATE_TEST_DATABASE_URL" in environ:
        raise error
    pytest.skip(f"PostgreSQL test database is unavailable: {error}")


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    previous_url = environ.get("DATABASE_URL")
    environ["DATABASE_URL"] = TEST_DATABASE_URL
    get_settings.cache_clear()
    try:
        command.upgrade(Config("alembic.ini"), "head")
    except (OSError, SQLAlchemyError) as error:
        handle_database_unavailable(error)
    try:
        yield
    finally:
        if previous_url is None:
            environ.pop("DATABASE_URL", None)
        else:
            environ["DATABASE_URL"] = previous_url
        get_settings.cache_clear()


@pytest.fixture
async def database_engine(
    migrated_database: None,
) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as error:
        await engine.dispose()
        handle_database_unavailable(error)

    try:
        yield engine
    finally:
        await engine.dispose()


@asynccontextmanager
async def started_app(app: object) -> AsyncIterator[None]:
    # FastAPI exposes the configured lifespan context through its router.
    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        yield


async def get_table_names(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_table_names()
        )
