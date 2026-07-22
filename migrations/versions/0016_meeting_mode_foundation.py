"""Add Meeting Mode foundation.

Revision ID: 0016_meeting_mode_foundation
Revises: 0015_pwa_remaining_screens
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_meeting_mode_foundation"
down_revision: str | None = "0015_pwa_remaining_screens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="planned"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("primary_topic_id", sa.Uuid()),
        sa.Column("summary", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "type IN ('lead','team','client_sync','steering','one_to_one','other')",
            name="ck_meetings_type",
        ),
        sa.CheckConstraint(
            "status IN ('planned','active','processing','review_required',"
            "'completed','cancelled')",
            name="ck_meetings_status",
        ),
        sa.CheckConstraint("char_length(btrim(title)) > 0", name="ck_meetings_title"),
        sa.CheckConstraint(
            "ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at",
            name="ck_meetings_time_order",
        ),
        sa.CheckConstraint(
            "status != 'planned' OR (started_at IS NULL AND ended_at IS NULL)",
            name="ck_meetings_planned_times",
        ),
        sa.CheckConstraint(
            "status != 'active' OR (started_at IS NOT NULL AND ended_at IS NULL)",
            name="ck_meetings_active_times",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR "
            "(started_at IS NOT NULL AND ended_at IS NOT NULL)",
            name="ck_meetings_completed_times",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["primary_topic_id"], ["topics.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meetings_user_status", "meetings", ["user_id", "status"])
    op.create_index("ix_meetings_user_started", "meetings", ["user_id", "started_at"])
    op.create_index(
        "uq_meetings_user_active",
        "meetings",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    for table, target, target_table, unique_name in (
        ("meeting_participants", "person_id", "people", "uq_meeting_participants"),
        ("meeting_topics", "topic_id", "topics", "uq_meeting_topics"),
    ):
        op.create_table(
            table,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("meeting_id", sa.Uuid(), nullable=False),
            sa.Column(target, sa.Uuid(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["meeting_id"], ["meetings.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                [target], [f"{target_table}.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("meeting_id", target, name=unique_name),
        )
        op.create_index(f"ix_{table}_user", table, ["user_id"])
        op.create_index(f"ix_{table}_{target.removesuffix('_id')}", table, [target])

    op.create_table(
        "meeting_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("note_id", name="uq_meeting_notes_note"),
    )
    op.create_index("ix_meeting_notes_meeting", "meeting_notes", ["meeting_id"])
    op.create_index("ix_meeting_notes_user", "meeting_notes", ["user_id"])

    op.create_table(
        "meeting_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("previous_status", sa.String(32)),
        sa.Column("new_status", sa.String(32), nullable=False),
        sa.Column("telegram_update_id", sa.BigInteger()),
        sa.Column("client_action_id", sa.Uuid()),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "event_type IN ('created','started','ended','cancelled')",
            name="ck_meeting_events_type",
        ),
        sa.CheckConstraint(
            "num_nonnulls(telegram_update_id, client_action_id) <= 1",
            name="ck_meeting_events_one_origin",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "telegram_update_id",
            "event_type",
            name="uq_meeting_events_telegram",
        ),
        sa.UniqueConstraint(
            "user_id", "client_action_id", "event_type", name="uq_meeting_events_client"
        ),
    )
    op.create_index(
        "ix_meeting_events_meeting_created",
        "meeting_events",
        ["meeting_id", "created_at"],
    )

    op.create_table(
        "meeting_setup_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid()),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("step", sa.String(32), nullable=False, server_default="type"),
        sa.Column(
            "context",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("prompt_message_id", sa.BigInteger()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('open','completed','cancelled','expired')",
            name="ck_meeting_setup_sessions_status",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_meeting_setup_sessions_expiration"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_meeting_setup_sessions_user_open",
        "meeting_setup_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        "ix_meeting_setup_sessions_expires", "meeting_setup_sessions", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("meeting_setup_sessions")
    op.drop_table("meeting_events")
    op.drop_table("meeting_notes")
    op.drop_table("meeting_topics")
    op.drop_table("meeting_participants")
    op.drop_table("meetings")
