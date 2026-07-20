import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.mark.integration
async def test_database_connection(database_engine: AsyncEngine) -> None:
    async with database_engine.connect() as connection:
        result = await connection.scalar(text("SELECT 1"))

    assert result == 1
