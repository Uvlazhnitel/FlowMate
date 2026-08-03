import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import TypedDict
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from flowmate.db.session import create_engine
from tests.conftest import TEST_DATABASE_URL, reset_test_database

PREVIOUS_HEAD = "0023_planner_manual_queue"
REMOVAL_HEAD = "0024_remove_meeting_mode"
MEETING_TABLES = {
    "meeting_agenda_entries",
    "meeting_work_items",
    "meeting_review_items",
    "meeting_reviews",
    "meeting_setup_sessions",
    "meeting_events",
    "meeting_notes",
    "meeting_topics",
    "meeting_participants",
    "meetings",
}
MEETING_DRAFT_COLUMNS = {
    "meeting_id",
    "capture_sequence",
    "capture_review_status",
    "capture_context",
    "overall_confidence",
}


class RemovalState(TypedDict):
    tables: set[str]
    draft_columns: set[str]
    index_predicate: str
    job_constraint: str
    revision: str
    note_count: int
    work_item_count: int


@pytest.fixture
def previous_head_database(migrated_database: None) -> Iterator[Config]:
    config = Config("alembic.ini")
    asyncio.run(reset_test_database(TEST_DATABASE_URL))
    command.upgrade(config, PREVIOUS_HEAD)
    try:
        yield config
    finally:
        asyncio.run(reset_test_database(TEST_DATABASE_URL))
        command.upgrade(config, "head")


async def seed_preserved_core_records(database_url: str) -> dict[str, UUID]:
    engine = create_engine(database_url)
    ids = {name: uuid4() for name in ("user", "note", "work_item", "draft")}
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, telegram_user_id, is_active) "
                    "VALUES (:id, 710001, true)"
                ),
                {"id": ids["user"]},
            )
            await connection.execute(
                text(
                    "INSERT INTO notes (id, user_id, source, content) "
                    "VALUES (:id, :user_id, 'manual', 'Preserved note')"
                ),
                {"id": ids["note"], "user_id": ids["user"]},
            )
            await connection.execute(
                text(
                    "INSERT INTO work_items "
                    "(id, user_id, type, title, status, priority) "
                    "VALUES (:id, :user_id, 'agenda_item', 'Preserved agenda item', "
                    "'active', 'normal')"
                ),
                {"id": ids["work_item"], "user_id": ids["user"]},
            )
            await connection.execute(
                text(
                    "INSERT INTO draft_sessions "
                    "(id, user_id, source_note_id, status, expires_at) "
                    "VALUES (:id, :user_id, :note_id, 'parsing', :expires_at)"
                ),
                {
                    "id": ids["draft"],
                    "user_id": ids["user"],
                    "note_id": ids["note"],
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                },
            )
    finally:
        await engine.dispose()
    return ids


async def read_removal_state(database_url: str, ids: dict[str, UUID]) -> RemovalState:
    engine = create_engine(database_url)
    try:
        async with engine.connect() as connection:
            table_names = set(
                await connection.run_sync(
                    lambda sync_connection: inspect(sync_connection).get_table_names()
                )
            )
            draft_columns = {
                column["name"]
                for column in await connection.run_sync(
                    lambda sync_connection: inspect(sync_connection).get_columns(
                        "draft_sessions"
                    )
                )
            }
            index_predicate = await connection.scalar(
                text(
                    "SELECT pg_get_expr(index.indpred, index.indrelid) "
                    "FROM pg_index AS index "
                    "JOIN pg_class AS relation ON relation.oid = index.indexrelid "
                    "WHERE relation.relname = 'uq_draft_sessions_user_open'"
                )
            )
            job_constraint = await connection.scalar(
                text(
                    "SELECT pg_get_constraintdef(constraint_row.oid) "
                    "FROM pg_constraint AS constraint_row "
                    "WHERE constraint_row.conname = 'ck_ai_processing_jobs_kind'"
                )
            )
            revision = str(
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
            )
            note_count = int(
                await connection.scalar(
                    text("SELECT count(*) FROM notes WHERE id = :id"),
                    {"id": ids["note"]},
                )
                or 0
            )
            work_item_count = int(
                await connection.scalar(
                    text("SELECT count(*) FROM work_items WHERE id = :id"),
                    {"id": ids["work_item"]},
                )
                or 0
            )
        return {
            "tables": table_names,
            "draft_columns": draft_columns,
            "index_predicate": str(index_predicate),
            "job_constraint": str(job_constraint),
            "revision": revision,
            "note_count": note_count,
            "work_item_count": work_item_count,
        }
    finally:
        await engine.dispose()


async def open_draft_uniqueness_is_enforced(
    database_url: str, *, user_id: UUID
) -> bool:
    engine = create_engine(database_url)
    try:
        async with engine.begin() as connection:
            note_id = uuid4()
            await connection.execute(
                text(
                    "INSERT INTO notes (id, user_id, source, content) "
                    "VALUES (:id, :user_id, 'manual', 'Second note')"
                ),
                {"id": note_id, "user_id": user_id},
            )
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO draft_sessions "
                        "(id, user_id, source_note_id, status, expires_at) "
                        "VALUES (:id, :user_id, :note_id, 'ready', :expires_at)"
                    ),
                    {
                        "id": uuid4(),
                        "user_id": user_id,
                        "note_id": note_id,
                        "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    },
                )
        except IntegrityError:
            return True
        return False
    finally:
        await engine.dispose()


async def seed_guarded_data(database_url: str, case: str) -> str:
    engine = create_engine(database_url)
    user_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (id, telegram_user_id, is_active) "
                    "VALUES (:id, 710002, true)"
                ),
                {"id": user_id},
            )
            if case == "meeting":
                await connection.execute(
                    text(
                        "INSERT INTO meetings (id, user_id, title, type, status) "
                        "VALUES (:id, :user_id, 'Legacy meeting', 'other', 'planned')"
                    ),
                    {"id": uuid4(), "user_id": user_id},
                )
                return "meetings=1"
            if case == "job":
                await connection.execute(
                    text(
                        "INSERT INTO ai_processing_jobs "
                        "(id, user_id, job_kind, entity_id, operation_key, "
                        "prompt_name, prompt_version) VALUES "
                        "(:id, :user_id, 'meeting_capture_parse', :entity_id, "
                        "'legacy-job', 'meeting-capture', 'legacy-v1')"
                    ),
                    {"id": uuid4(), "user_id": user_id, "entity_id": uuid4()},
                )
                return "ai_processing_jobs (Meeting kinds)=1"

            note_id = uuid4()
            await connection.execute(
                text(
                    "INSERT INTO notes (id, user_id, source, content) "
                    "VALUES (:id, :user_id, 'manual', 'Legacy capture')"
                ),
                {"id": note_id, "user_id": user_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO draft_sessions "
                    "(id, user_id, source_note_id, status, expires_at, "
                    "overall_confidence) VALUES "
                    "(:id, :user_id, :note_id, 'confirmed', :expires_at, 0.8)"
                ),
                {
                    "id": uuid4(),
                    "user_id": user_id,
                    "note_id": note_id,
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                },
            )
            return "draft_sessions (Meeting fields)=1"
    finally:
        await engine.dispose()


async def read_pre_removal_state(database_url: str) -> tuple[str, set[str], set[str]]:
    engine = create_engine(database_url)
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            tables = set(
                await connection.run_sync(
                    lambda sync_connection: inspect(sync_connection).get_table_names()
                )
            )
            columns = {
                column["name"]
                for column in await connection.run_sync(
                    lambda sync_connection: inspect(sync_connection).get_columns(
                        "draft_sessions"
                    )
                )
            }
        return str(revision), tables, columns
    finally:
        await engine.dispose()


def test_meeting_mode_removal_upgrades_previous_head(
    previous_head_database: Config,
) -> None:
    ids = asyncio.run(seed_preserved_core_records(TEST_DATABASE_URL))

    command.upgrade(previous_head_database, "head")

    state = asyncio.run(read_removal_state(TEST_DATABASE_URL, ids))
    assert state["revision"] == REMOVAL_HEAD
    assert MEETING_TABLES.isdisjoint(state["tables"])
    assert MEETING_DRAFT_COLUMNS.isdisjoint(state["draft_columns"])
    predicate = str(state["index_predicate"])
    assert "meeting_id" not in predicate
    assert all(
        status in predicate for status in ("parsing", "needs_clarification", "ready")
    )
    constraint = str(state["job_constraint"])
    assert "draft_parse" in constraint
    assert "draft_refine" in constraint
    assert "meeting_capture_parse" not in constraint
    assert "meeting_review_generate" not in constraint
    assert state["note_count"] == 1
    assert state["work_item_count"] == 1
    assert asyncio.run(
        open_draft_uniqueness_is_enforced(TEST_DATABASE_URL, user_id=ids["user"])
    )
    command.check(previous_head_database)


@pytest.mark.parametrize("case", ["meeting", "job", "draft"])
def test_meeting_mode_removal_refuses_legacy_data_before_schema_changes(
    previous_head_database: Config,
    case: str,
) -> None:
    expected_count = asyncio.run(seed_guarded_data(TEST_DATABASE_URL, case))

    with pytest.raises(RuntimeError) as error:
        command.upgrade(previous_head_database, "head")

    message = str(error.value)
    assert expected_count in message
    assert "Export or archive" in message
    revision, tables, columns = asyncio.run(read_pre_removal_state(TEST_DATABASE_URL))
    assert revision == PREVIOUS_HEAD
    assert MEETING_TABLES <= tables
    assert MEETING_DRAFT_COLUMNS <= columns


def test_meeting_mode_removal_downgrade_is_explicitly_irreversible(
    previous_head_database: Config,
) -> None:
    command.upgrade(previous_head_database, "head")

    with pytest.raises(RuntimeError, match="irreversible"):
        command.downgrade(previous_head_database, PREVIOUS_HEAD)

    revision, _, _ = asyncio.run(read_pre_removal_state(TEST_DATABASE_URL))
    assert revision == REMOVAL_HEAD
