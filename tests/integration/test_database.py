from typing import Any, cast

import pytest
from sqlalchemy import String, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from flowmate.db.health import database_is_ready
from tests.conftest import get_table_names


@pytest.mark.integration
async def test_database_connection(database_engine: AsyncEngine) -> None:
    async with database_engine.connect() as connection:
        result = await connection.scalar(text("SELECT 1"))

    assert result == 1
    assert await database_is_ready(database_engine) is True


@pytest.mark.integration
async def test_migrations_create_users_table(database_engine: AsyncEngine) -> None:
    table_names = await get_table_names(database_engine)

    assert "alembic_version" in table_names
    assert "users" in table_names


@pytest.mark.integration
async def test_users_schema_matches_metadata(database_engine: AsyncEngine) -> None:
    async with database_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]: column
                for column in inspect(sync_connection).get_columns("users")
            }
        )
        unique_constraints = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_unique_constraints(
                "users"
            )
        )
        check_constraints = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "users"
            )
        )
        revision = await connection.scalar(
            text("SELECT version_num FROM alembic_version")
        )

    assert columns["telegram_user_id"]["nullable"] is True
    assert columns["display_name"]["nullable"] is True
    display_name_column = cast(dict[str, Any], columns["display_name"])
    display_name_type = cast(String, display_name_column["type"])
    assert display_name_type.length == 255
    assert {constraint["name"] for constraint in unique_constraints} >= {
        "users_telegram_user_id_key"
    }
    assert {constraint["name"] for constraint in check_constraints} >= {
        "ck_users_telegram_user_id_positive"
    }
    assert revision == "0003_expand_users"
