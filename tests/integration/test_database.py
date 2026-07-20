import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import get_table_names


@pytest.mark.integration
async def test_database_connection(database_engine: AsyncEngine) -> None:
    async with database_engine.connect() as connection:
        result = await connection.scalar(text("SELECT 1"))

    assert result == 1


@pytest.mark.integration
async def test_migrations_create_users_table(database_engine: AsyncEngine) -> None:
    table_names = await get_table_names(database_engine)

    assert "alembic_version" in table_names
    assert "users" in table_names
