import asyncio
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import String, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from flowmate.db.health import database_is_ready
from flowmate.db.session import create_engine
from tests.conftest import (
    APPLICATION_TABLES,
    TEST_DATABASE_URL,
    database_has_table,
    get_table_names,
)


async def seed_backfill_work_item(database_url: str) -> tuple[UUID, datetime]:
    engine = create_engine(database_url)
    user_id = uuid4()
    item_id = uuid4()
    due_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, telegram_user_id, is_active) "
                    "VALUES (:id, :telegram_user_id, true)"
                ),
                {"id": user_id, "telegram_user_id": 699_001},
            )
            await connection.execute(
                text(
                    "INSERT INTO work_items "
                    "(id, user_id, type, title, status, priority, due_at) "
                    "VALUES (:id, :user_id, 'task', 'Backfill task', "
                    "'active', 'normal', :due_at)"
                ),
                {"id": item_id, "user_id": user_id, "due_at": due_at},
            )
    finally:
        await engine.dispose()
    return item_id, due_at


async def read_backfilled_reminder(
    database_url: str,
    item_id: UUID,
) -> tuple[str, datetime] | None:
    engine = create_engine(database_url)
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT schedule_kind, reference_at FROM reminders "
                        "WHERE work_item_id = :item_id"
                    ),
                    {"item_id": item_id},
                )
            ).one_or_none()
        return (row[0], row[1]) if row is not None else None
    finally:
        await engine.dispose()


async def delete_backfill_user(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM users WHERE telegram_user_id = 699001")
            )
    finally:
        await engine.dispose()


async def seed_planner_status_event(database_url: str) -> UUID:
    engine = create_engine(database_url)
    user_id = uuid4()
    item_id = uuid4()
    event_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, telegram_user_id, is_active) "
                    "VALUES (:id, :telegram_user_id, true)"
                ),
                {"id": user_id, "telegram_user_id": 699_002},
            )
            await connection.execute(
                text(
                    "INSERT INTO work_items "
                    "(id, user_id, type, title, status, priority) "
                    "VALUES (:id, :user_id, 'task', 'Planner event downgrade', "
                    "'active', 'normal')"
                ),
                {"id": item_id, "user_id": user_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO work_item_events "
                    "(id, user_id, work_item_id, event_type) "
                    "VALUES (:id, :user_id, :work_item_id, "
                    "'planner_status_changed')"
                ),
                {"id": event_id, "user_id": user_id, "work_item_id": item_id},
            )
    finally:
        await engine.dispose()
    return event_id


async def read_event_type(database_url: str, event_id: UUID) -> str | None:
    engine = create_engine(database_url)
    try:
        async with engine.connect() as connection:
            return cast(
                str | None,
                await connection.scalar(
                    text(
                        "SELECT event_type FROM work_item_events WHERE id = :event_id"
                    ),
                    {"event_id": event_id},
                ),
            )
    finally:
        await engine.dispose()


async def delete_planner_event_user(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM users WHERE telegram_user_id = 699002")
            )
    finally:
        await engine.dispose()


def test_migrations_upgrade_from_previous_stage_revision(
    migrated_database: None,
) -> None:
    alembic_config = Config("alembic.ini")
    try:
        command.downgrade(alembic_config, "0009_create_reminders")
        assert asyncio.run(database_has_table(TEST_DATABASE_URL, "draft_sessions"))
        assert asyncio.run(database_has_table(TEST_DATABASE_URL, "draft_items"))
        assert asyncio.run(
            database_has_table(TEST_DATABASE_URL, "work_item_action_sessions")
        )
        assert asyncio.run(database_has_table(TEST_DATABASE_URL, "reminders"))
        item_id, due_at = asyncio.run(seed_backfill_work_item(TEST_DATABASE_URL))
        command.upgrade(alembic_config, "head")
        assert asyncio.run(read_backfilled_reminder(TEST_DATABASE_URL, item_id)) == (
            "exact",
            due_at,
        )
        asyncio.run(delete_backfill_user(TEST_DATABASE_URL))
        for table_name in APPLICATION_TABLES:
            assert asyncio.run(database_has_table(TEST_DATABASE_URL, table_name))
    finally:
        command.upgrade(alembic_config, "head")


def test_notification_preferences_migration_from_0010(
    migrated_database: None,
) -> None:
    alembic_config = Config("alembic.ini")
    try:
        command.downgrade(alembic_config, "0010_connect_work_item_reminders")
        assert not asyncio.run(
            database_has_table(TEST_DATABASE_URL, "user_notification_preferences")
        )
        command.upgrade(alembic_config, "head")
        assert asyncio.run(
            database_has_table(TEST_DATABASE_URL, "user_notification_preferences")
        )
    finally:
        command.upgrade(alembic_config, "head")


def test_planner_event_survives_downgrade_to_0014(migrated_database: None) -> None:
    alembic_config = Config("alembic.ini")
    event_id = asyncio.run(seed_planner_status_event(TEST_DATABASE_URL))
    try:
        command.downgrade(alembic_config, "0014_pwa_operations")
        assert asyncio.run(read_event_type(TEST_DATABASE_URL, event_id)) == "updated"
        asyncio.run(delete_planner_event_user(TEST_DATABASE_URL))
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
    assert revision == "0020_stage8_stabilization"


def test_pwa_auth_migration_from_0012(migrated_database: None) -> None:
    alembic_config = Config("alembic.ini")
    try:
        command.downgrade(alembic_config, "0012_main_menu_navigation")
        assert not asyncio.run(database_has_table(TEST_DATABASE_URL, "pwa_login_codes"))
        assert not asyncio.run(database_has_table(TEST_DATABASE_URL, "pwa_sessions"))
        command.upgrade(alembic_config, "head")
        assert asyncio.run(database_has_table(TEST_DATABASE_URL, "pwa_login_codes"))
        assert asyncio.run(database_has_table(TEST_DATABASE_URL, "pwa_sessions"))
    finally:
        command.upgrade(alembic_config, "head")


@pytest.mark.integration
async def test_reminder_schema_has_delivery_constraints(
    database_engine: AsyncEngine,
) -> None:
    async with database_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]: column
                for column in inspect(sync_connection).get_columns("reminders")
            }
        )
        checks = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "reminders"
            )
        )
        uniques = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_unique_constraints(
                "reminders"
            )
        )
        indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes("reminders")
        )

    assert columns["work_item_id"]["nullable"] is True
    assert columns["processing_token"]["nullable"] is True
    assert columns["next_attempt_at"]["nullable"] is True
    assert columns["reference_at"]["nullable"] is True
    assert columns["schedule_kind"]["nullable"] is False
    assert columns["digest_local_date"]["nullable"] is True
    assert columns["schedule_timezone"]["nullable"] is True
    assert {constraint["name"] for constraint in checks} >= {
        "ck_reminders_deduplication_key_not_blank",
        "ck_reminders_delivery_attempts_nonnegative",
        "ck_reminders_message_not_blank",
        "ck_reminders_status",
        "ck_reminders_type",
        "ck_reminders_schedule_kind",
        "ck_reminders_managed_reference",
    }
    assert {constraint["name"] for constraint in uniques} >= {
        "uq_reminders_user_deduplication_key",
        "uq_reminders_user_digest_local_date",
    }
    assert {index["name"] for index in indexes} >= {
        "ix_reminders_status_scheduled_at",
        "ix_reminders_user_status",
        "ix_reminders_work_item_id",
        "ix_reminders_work_item_status_kind",
    }


@pytest.mark.integration
async def test_notification_preferences_schema(database_engine: AsyncEngine) -> None:
    async with database_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]: column
                for column in inspect(sync_connection).get_columns(
                    "user_notification_preferences"
                )
            }
        )
        checks = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "user_notification_preferences"
            )
        )

    assert {
        "timezone",
        "morning_digest_enabled",
        "morning_digest_time",
        "evening_digest_enabled",
        "evening_digest_time",
        "quiet_hours_enabled",
        "quiet_hours_start",
        "quiet_hours_end",
        "default_snooze_minutes",
        "send_empty_digests",
    } <= set(columns)
    assert {constraint["name"] for constraint in checks} >= {
        "ck_user_notification_preferences_timezone_not_blank",
        "ck_user_notification_preferences_snooze_range",
        "ck_user_notification_preferences_quiet_range",
    }


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
    assert columns["content"]["nullable"] is True
    assert columns["transcript_redacted_at"]["nullable"] is True
    assert columns["source"]["nullable"] is False
    assert columns["telegram_update_id"]["nullable"] is True
    assert columns["source_draft_item_id"]["nullable"] is True
    assert columns["created_at"]["nullable"] is False
    assert {constraint["name"] for constraint in unique_constraints} >= {
        "notes_telegram_update_id_key",
        "uq_notes_source_draft_item_id",
    }
    assert {constraint["name"] for constraint in check_constraints} >= {
        "ck_notes_content_or_redacted",
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
        event_columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]: column
                for column in inspect(sync_connection).get_columns("work_item_events")
            }
        )
        event_uniques = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_unique_constraints(
                "work_item_events"
            )
        )
        action_checks = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "work_item_action_sessions"
            )
        )
        action_columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]: column
                for column in inspect(sync_connection).get_columns(
                    "work_item_action_sessions"
                )
            }
        )
        action_uniques = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_unique_constraints(
                "work_item_action_sessions"
            )
        )
        action_indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes(
                "work_item_action_sessions"
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
    assert event_columns["telegram_update_id"]["nullable"] is True
    assert {constraint["name"] for constraint in event_uniques} >= {
        "uq_work_item_events_telegram_update_id"
    }
    assert {constraint["name"] for constraint in action_checks} >= {
        "ck_work_item_action_sessions_action",
        "ck_work_item_action_sessions_expiration",
        "ck_work_item_action_sessions_status",
        "ck_work_item_action_sessions_telegram_update_id_positive",
    }
    assert action_columns["telegram_update_id"]["nullable"] is True
    assert {constraint["name"] for constraint in action_uniques} >= {
        "uq_work_item_action_sessions_telegram_update_id"
    }
    assert {index["name"] for index in action_indexes} >= {
        "ix_work_item_action_sessions_expires_at",
        "uq_work_item_action_sessions_user_open",
    }


@pytest.mark.integration
async def test_meeting_capture_schema_constraints(database_engine: AsyncEngine) -> None:
    async with database_engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]: column
                for column in inspect(sync_connection).get_columns("draft_sessions")
            }
        )
        checks = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_check_constraints(
                "draft_sessions"
            )
        )
        uniques = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_unique_constraints(
                "draft_sessions"
            )
        )
        indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes(
                "draft_sessions"
            )
        )
        meeting_event_indexes = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_indexes(
                "meeting_events"
            )
        )

    assert columns["meeting_id"]["nullable"] is True
    assert columns["capture_sequence"]["nullable"] is True
    assert columns["capture_context"]["nullable"] is False
    assert {constraint["name"] for constraint in checks} >= {
        "ck_draft_sessions_capture_fields",
        "ck_draft_sessions_capture_review_status",
        "ck_draft_sessions_capture_sequence_positive",
        "ck_draft_sessions_overall_confidence",
    }
    assert {constraint["name"] for constraint in uniques} >= {
        "uq_draft_sessions_meeting_capture_sequence"
    }
    assert {index["name"] for index in indexes} >= {
        "ix_draft_sessions_meeting_capture",
        "uq_draft_sessions_user_open",
    }
    assert {index["name"] for index in meeting_event_indexes} >= {
        "ix_meeting_events_user_created_id"
    }
