"""Persist Work and Personal workspace separation.

Revision ID: 0021_workspace_separation
Revises: 0020_stage8_stabilization
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_workspace_separation"
down_revision: str | None = "0020_stage8_stabilization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCOPED_TABLES = (
    "topics",
    "work_items",
    "notes",
    "draft_sessions",
    "meetings",
    "reminders",
    "work_item_action_sessions",
    "meeting_setup_sessions",
)


def _add_workspace(table: str) -> None:
    op.add_column(
        table,
        sa.Column(
            "workspace",
            sa.String(length=16),
            nullable=False,
            server_default="personal",
        ),
    )
    op.create_check_constraint(
        f"ck_{table}_workspace",
        table,
        "workspace IN ('personal','work')",
    )


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "active_workspace",
            sa.String(length=16),
            nullable=False,
            server_default="personal",
        ),
    )
    op.create_check_constraint(
        "ck_users_active_workspace",
        "users",
        "active_workspace IN ('personal','work')",
    )
    for table in SCOPED_TABLES:
        _add_workspace(table)

    op.drop_index("ix_topics_user_active", table_name="topics")
    op.drop_index("uq_topics_user_normalized_name", table_name="topics")
    op.create_index(
        "ix_topics_user_workspace_active",
        "topics",
        ["user_id", "workspace", "is_active"],
    )
    op.create_index(
        "uq_topics_user_workspace_normalized_name",
        "topics",
        ["user_id", "workspace", sa.text("lower(btrim(name))")],
        unique=True,
    )

    op.drop_index("ix_work_items_user_status", table_name="work_items")
    op.drop_index("ix_work_items_user_due_at", table_name="work_items")
    op.drop_index("ix_work_items_user_planner", table_name="work_items")
    op.create_index(
        "ix_work_items_user_workspace_status",
        "work_items",
        ["user_id", "workspace", "status"],
    )
    op.create_index(
        "ix_work_items_user_workspace_due_at",
        "work_items",
        ["user_id", "workspace", "due_at"],
    )
    op.create_index(
        "ix_work_items_user_workspace_planner",
        "work_items",
        ["user_id", "workspace", "planner_status"],
    )

    op.drop_index("ix_notes_user_id_created_at", table_name="notes")
    op.drop_index("ix_notes_user_inbox_disposition", table_name="notes")
    op.create_index(
        "ix_notes_user_workspace_created_at",
        "notes",
        ["user_id", "workspace", "created_at"],
    )
    op.create_index(
        "ix_notes_user_workspace_inbox_disposition",
        "notes",
        ["user_id", "workspace", "inbox_disposition", "created_at"],
    )

    op.drop_index("ix_draft_sessions_user_status", table_name="draft_sessions")
    op.create_index(
        "ix_draft_sessions_user_workspace_status",
        "draft_sessions",
        ["user_id", "workspace", "status"],
    )

    op.drop_index("ix_meetings_user_status", table_name="meetings")
    op.drop_index("ix_meetings_user_started", table_name="meetings")
    op.create_index(
        "ix_meetings_user_workspace_status",
        "meetings",
        ["user_id", "workspace", "status"],
    )
    op.create_index(
        "ix_meetings_user_workspace_started",
        "meetings",
        ["user_id", "workspace", "started_at"],
    )

    op.drop_index("ix_reminders_user_status", table_name="reminders")
    op.drop_constraint(
        "uq_reminders_user_deduplication_key",
        "reminders",
        type_="unique",
    )
    op.drop_constraint(
        "uq_reminders_user_digest_local_date",
        "reminders",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_reminders_user_workspace_deduplication_key",
        "reminders",
        ["user_id", "workspace", "deduplication_key"],
    )
    op.create_unique_constraint(
        "uq_reminders_user_workspace_digest_local_date",
        "reminders",
        ["user_id", "workspace", "type", "digest_local_date"],
    )
    op.create_index(
        "ix_reminders_user_workspace_status",
        "reminders",
        ["user_id", "workspace", "status"],
    )


def downgrade() -> None:
    op.execute("UPDATE users SET active_workspace = 'personal'")
    for table in SCOPED_TABLES:
        op.execute(f"UPDATE {table} SET workspace = 'personal'")

    op.drop_index("ix_reminders_user_workspace_status", table_name="reminders")
    op.drop_constraint(
        "uq_reminders_user_workspace_digest_local_date",
        "reminders",
        type_="unique",
    )
    op.drop_constraint(
        "uq_reminders_user_workspace_deduplication_key",
        "reminders",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_reminders_user_deduplication_key",
        "reminders",
        ["user_id", "deduplication_key"],
    )
    op.create_unique_constraint(
        "uq_reminders_user_digest_local_date",
        "reminders",
        ["user_id", "type", "digest_local_date"],
    )
    op.create_index("ix_reminders_user_status", "reminders", ["user_id", "status"])

    op.drop_index("ix_meetings_user_workspace_started", table_name="meetings")
    op.drop_index("ix_meetings_user_workspace_status", table_name="meetings")
    op.create_index("ix_meetings_user_started", "meetings", ["user_id", "started_at"])
    op.create_index("ix_meetings_user_status", "meetings", ["user_id", "status"])

    op.drop_index(
        "ix_draft_sessions_user_workspace_status", table_name="draft_sessions"
    )
    op.create_index(
        "ix_draft_sessions_user_status",
        "draft_sessions",
        ["user_id", "status"],
    )

    op.drop_index("ix_notes_user_workspace_inbox_disposition", table_name="notes")
    op.drop_index("ix_notes_user_workspace_created_at", table_name="notes")
    op.create_index(
        "ix_notes_user_inbox_disposition",
        "notes",
        ["user_id", "inbox_disposition", "created_at"],
    )
    op.create_index("ix_notes_user_id_created_at", "notes", ["user_id", "created_at"])

    op.drop_index("ix_work_items_user_workspace_planner", table_name="work_items")
    op.drop_index("ix_work_items_user_workspace_due_at", table_name="work_items")
    op.drop_index("ix_work_items_user_workspace_status", table_name="work_items")
    op.create_index(
        "ix_work_items_user_planner",
        "work_items",
        ["user_id", "planner_status"],
    )
    op.create_index("ix_work_items_user_due_at", "work_items", ["user_id", "due_at"])
    op.create_index("ix_work_items_user_status", "work_items", ["user_id", "status"])

    op.drop_index("uq_topics_user_workspace_normalized_name", table_name="topics")
    op.drop_index("ix_topics_user_workspace_active", table_name="topics")
    op.create_index(
        "uq_topics_user_normalized_name",
        "topics",
        ["user_id", sa.text("lower(btrim(name))")],
        unique=True,
    )
    op.create_index("ix_topics_user_active", "topics", ["user_id", "is_active"])

    for table in reversed(SCOPED_TABLES):
        op.drop_constraint(f"ck_{table}_workspace", table, type_="check")
        op.drop_column(table, "workspace")
    op.drop_constraint("ck_users_active_workspace", "users", type_="check")
    op.drop_column("users", "active_workspace")
