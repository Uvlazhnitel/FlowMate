import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from os import environ, getenv
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from flowmate.core.config import get_settings
from flowmate.db.session import create_engine

TEST_DATABASE_URL = getenv(
    "TEST_DATABASE_URL",
    getenv(
        "FLOWMATE_TEST_DATABASE_URL",
        "postgresql+asyncpg://flowmate_test:flowmate_test@localhost:5433/flowmate_test",
    ),
)

APPLICATION_TABLES = (
    "users",
    "notes",
    "draft_sessions",
    "draft_items",
    "topics",
    "people",
    "work_items",
    "work_item_people",
    "work_item_relations",
    "note_links",
    "work_item_events",
    "work_item_action_sessions",
    "reminders",
    "user_notification_preferences",
)

UNIT_ENVIRONMENT_VARIABLES = (
    "APP_ENV",
    "APP_DEBUG",
    "APP_HOST",
    "APP_PORT",
    "APP_API_KEY",
    "DATABASE_URL",
    "CORS_ORIGINS",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USER_IDS",
    "SPEECH_PROVIDER",
    "OPENAI_API_KEY",
    "SPEECH_MODEL",
    "SPEECH_LANGUAGE",
    "SPEECH_TIMEOUT_SECONDS",
    "SPEECH_MAX_FILE_SIZE_BYTES",
    "AI_PROVIDER",
    "AI_MODEL",
    "AI_TIMEOUT_SECONDS",
    "APP_TIMEZONE",
    "APP_ACTIVE_WORKSPACE",
    "AI_HIGH_CONFIDENCE_THRESHOLD",
    "AI_CLARIFICATION_CONFIDENCE_THRESHOLD",
    "DRAFT_TTL_HOURS",
    "WORK_ITEM_ACTION_TTL_MINUTES",
    "SCHEDULER_INTERVAL_SECONDS",
    "REMINDER_BATCH_SIZE",
    "REMINDER_MAX_ATTEMPTS",
    "REMINDER_RETRY_DELAY_SECONDS",
    "REMINDER_PROCESSING_TIMEOUT_SECONDS",
    "REMINDER_DELIVERY_TIMEOUT_SECONDS",
    "DEADLINE_REMINDER_LEAD_MINUTES",
    "DEFAULT_MORNING_DIGEST_TIME",
    "DEFAULT_EVENING_DIGEST_TIME",
    "DEFAULT_QUIET_HOURS_START",
    "DEFAULT_QUIET_HOURS_END",
    "DEFAULT_SNOOZE_MINUTES",
    "LOG_LEVEL",
    "FLOWMATE_ENVIRONMENT",
    "FLOWMATE_API_DOCS_ENABLED",
    "FLOWMATE_API_BEARER_TOKEN",
    "FLOWMATE_DATABASE_URL",
    "FLOWMATE_TELEGRAM_BOT_TOKEN",
    "FLOWMATE_TELEGRAM_ALLOWED_USER_IDS",
    "FLOWMATE_LOG_LEVEL",
)


@pytest.fixture(autouse=True)
def isolate_test_environment(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    if "unit" in Path(str(request.node.path)).parts:
        for variable in UNIT_ENVIRONMENT_VARIABLES:
            monkeypatch.delenv(variable, raising=False)
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def validate_test_database_url(database_url: str) -> None:
    url = make_url(database_url)
    if (
        url.drivername != "postgresql+asyncpg"
        or url.database is None
        or not url.database.endswith("_test")
    ):
        raise ValueError(
            "TEST_DATABASE_URL must use postgresql+asyncpg and a database "
            "name ending in '_test'"
        )


def handle_database_unavailable(error: Exception) -> None:
    if "TEST_DATABASE_URL" in environ or "FLOWMATE_TEST_DATABASE_URL" in environ:
        raise error
    pytest.skip(f"PostgreSQL test database is unavailable: {error}")


async def database_has_table(database_url: str, table_name: str) -> bool:
    engine = create_engine(database_url)
    try:
        async with engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_table_names()
            )
        return table_name in table_names
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def migrated_database() -> Iterator[None]:
    validate_test_database_url(TEST_DATABASE_URL)
    previous_url = environ.get("DATABASE_URL")
    environ["DATABASE_URL"] = TEST_DATABASE_URL
    get_settings.cache_clear()
    alembic_config = Config("alembic.ini")
    try:
        command.downgrade(alembic_config, "base")
        for table_name in APPLICATION_TABLES:
            assert not asyncio.run(database_has_table(TEST_DATABASE_URL, table_name))
        command.upgrade(alembic_config, "head")
        for table_name in APPLICATION_TABLES:
            assert asyncio.run(database_has_table(TEST_DATABASE_URL, table_name))
    except (OSError, SQLAlchemyError) as error:
        handle_database_unavailable(error)
    try:
        yield
    finally:
        command.downgrade(alembic_config, "base")
        for table_name in APPLICATION_TABLES:
            assert not asyncio.run(database_has_table(TEST_DATABASE_URL, table_name))
        command.upgrade(alembic_config, "head")
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


@pytest.fixture
async def database_session(
    database_engine: AsyncEngine,
) -> AsyncIterator[AsyncSession]:
    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


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
