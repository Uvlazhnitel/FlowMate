import asyncio
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import String, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from flowmate.db.health import database_is_ready
from tests.conftest import (
    APPLICATION_TABLES,
    TEST_DATABASE_URL,
    database_has_table,
    get_table_names,
)


def test_migrations_upgrade_from_previous_stage_revision(
    migrated_database: None,
) -> None:
    alembic_config = Config("alembic.ini")
    try:
        command.downgrade(alembic_config, "0006_create_task_engine")
        assert asyncio.run(database_has_table(TEST_DATABASE_URL, "draft_sessions"))
        assert asyncio.run(database_has_table(TEST_DATABASE_URL, "draft_items"))
        for table_name in APPLICATION_TABLES:
            assert asyncio.run(database_has_table(TEST_DATABASE_URL, table_name))
        command.upgrade(alembic_config, "head")
        for table_name in APPLICATION_TABLES:
            assert asyncio.run(database_has_table(TEST_DATABASE_URL, table_name))
    finally:
        command.upgrade(alembic_config, "head")


@pytest.mark.integration
async def test_database_connection(database_engine: AsyncEngine) -> None:
    async with database_engine.connect() as connection:
        result = await connection.scalar(text("SELECT 1"))

    assert result == 1
    assert await database_is_ready(database_engine) is True


@pytest.mark.integration
async def test_migrations_create_application_tables(
    database_engine: AsyncEngine,
) -> None:
    table_names = await get_table_names(database_engine)

    assert "alembic_version" in table_names
    assert set(APPLICATION_TABLES) <= set(table_names)


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
    assert revision == "0007_draft_conversion"


@pytest.mark.integration
async def test_notes_schema_matches_metadata(database_engine: AsyncEngine) -> None:
    async with database_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]: column
                for column in inspect(sync_connection).get_columns("notes")
            }
        )
        unique_constraints = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_unique_constraints(
                "notes"
            )
        )
        check_constraints = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "notes"
            )
        )
        foreign_keys = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_foreign_keys("notes")
        )
        indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes("notes")
        )

    assert columns["user_id"]["nullable"] is False
    assert columns["content"]["nullable"] is False
    assert columns["source"]["nullable"] is False
    assert columns["telegram_update_id"]["nullable"] is True
    assert columns["source_draft_item_id"]["nullable"] is True
    assert columns["created_at"]["nullable"] is False
    assert {constraint["name"] for constraint in unique_constraints} >= {
        "notes_telegram_update_id_key",
        "uq_notes_source_draft_item_id",
    }
    assert {constraint["name"] for constraint in check_constraints} >= {
        "ck_notes_content_not_blank",
        "ck_notes_source",
        "ck_notes_source_update_consistency",
    }
    assert {foreign_key["name"] for foreign_key in foreign_keys} >= {
        "fk_notes_source_draft_item_id_draft_items",
        "fk_notes_user_id_users",
    }
    assert {index["name"] for index in indexes} >= {"ix_notes_user_id_created_at"}


@pytest.mark.integration
async def test_draft_schema_has_state_and_ownership_constraints(
    database_engine: AsyncEngine,
) -> None:
    async with database_engine.connect() as connection:
        session_checks = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "draft_sessions"
            )
        )
        session_indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes(
                "draft_sessions"
            )
        )
        item_checks = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "draft_items"
            )
        )
        item_foreign_keys = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_foreign_keys(
                "draft_items"
            )
        )

    assert {constraint["name"] for constraint in session_checks} >= {
        "ck_draft_sessions_expiration",
        "ck_draft_sessions_status",
    }
    assert {index["name"] for index in session_indexes} >= {
        "ix_draft_sessions_expires_at",
        "ix_draft_sessions_user_status",
        "uq_draft_sessions_user_open",
    }
    assert {constraint["name"] for constraint in item_checks} >= {
        "ck_draft_items_confidence",
        "ck_draft_items_position_positive",
        "ck_draft_items_readiness",
        "ck_draft_items_title_not_blank",
        "ck_draft_items_type",
    }
    assert {foreign_key["name"] for foreign_key in item_foreign_keys} >= {
        "fk_draft_items_draft_session_id_draft_sessions"
    }


@pytest.mark.integration
async def test_task_engine_schema_has_core_constraints(
    database_engine: AsyncEngine,
) -> None:
    async with database_engine.connect() as connection:
        work_item_checks = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "work_items"
            )
        )
        work_item_uniques = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_unique_constraints(
                "work_items"
            )
        )
        topic_indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes("topics")
        )
        note_link_checks = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "note_links"
            )
        )
        relation_checks = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "work_item_relations"
            )
        )

    assert {constraint["name"] for constraint in work_item_checks} >= {
        "ck_work_items_priority",
        "ck_work_items_status",
        "ck_work_items_title_not_blank",
        "ck_work_items_type",
    }
    assert {constraint["name"] for constraint in work_item_uniques} >= {
        "uq_work_items_source_draft_item_id"
    }
    assert {index["name"] for index in topic_indexes} >= {
        "ix_topics_user_active",
        "uq_topics_user_normalized_name",
    }
    assert {constraint["name"] for constraint in note_link_checks} >= {
        "ck_note_links_one_target"
    }
    assert {constraint["name"] for constraint in relation_checks} >= {
        "ck_work_item_relations_not_self",
        "ck_work_item_relations_type",
    }
