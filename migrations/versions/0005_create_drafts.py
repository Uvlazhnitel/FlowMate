"""Create persistent AI draft sessions and items.

Revision ID: 0005_create_drafts
Revises: 0004_create_notes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_create_drafts"
down_revision: str | None = "0004_create_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "draft_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_note_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("analysis_payload", postgresql.JSONB(), nullable=True),
        sa.Column("current_question", sa.Text(), nullable=True),
        sa.Column(
            "current_question_options",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("current_question_context", postgresql.JSONB(), nullable=True),
        sa.Column("current_question_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "processed_update_ids",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("processing_update_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('parsing', 'needs_clarification', 'ready', 'confirmed', "
            "'cancelled', 'expired', 'failed')",
            name="ck_draft_sessions_status",
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name="ck_draft_sessions_expiration"
        ),
        sa.ForeignKeyConstraint(
            ["source_note_id"],
            ["notes.id"],
            name="fk_draft_sessions_source_note_id_notes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_draft_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_draft_sessions"),
        sa.UniqueConstraint("source_note_id", name="uq_draft_sessions_source_note_id"),
    )
    op.create_index(
        "ix_draft_sessions_expires_at", "draft_sessions", ["expires_at"], unique=False
    )
    op.create_index(
        "ix_draft_sessions_user_status",
        "draft_sessions",
        ["user_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_draft_sessions_user_open",
        "draft_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('parsing', 'needs_clarification', 'ready')"
        ),
    )

    op.create_table(
        "draft_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("draft_session_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "people_candidates",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "topic_candidates",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("original_date_text", sa.Text(), nullable=True),
        sa.Column("normalized_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "notes",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "missing_fields",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "ambiguities",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("readiness", sa.String(length=32), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_draft_items_confidence",
        ),
        sa.CheckConstraint("position > 0", name="ck_draft_items_position_positive"),
        sa.CheckConstraint(
            "char_length(btrim(title)) > 0",
            name="ck_draft_items_title_not_blank",
        ),
        sa.CheckConstraint(
            "item_type IN ('task', 'follow_up', 'waiting', 'question', 'note', "
            "'decision', 'agenda_item', 'unknown')",
            name="ck_draft_items_type",
        ),
        sa.CheckConstraint(
            "readiness IN ('ready', 'clarification_required', 'unresolved')",
            name="ck_draft_items_readiness",
        ),
        sa.ForeignKeyConstraint(
            ["draft_session_id"],
            ["draft_sessions.id"],
            name="fk_draft_items_draft_session_id_draft_sessions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_draft_items"),
        sa.UniqueConstraint(
            "draft_session_id",
            "position",
            name="uq_draft_items_session_position",
        ),
    )
    op.create_index(
        "ix_draft_items_session",
        "draft_items",
        ["draft_session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_draft_items_session", table_name="draft_items")
    op.drop_table("draft_items")
    op.drop_index("uq_draft_sessions_user_open", table_name="draft_sessions")
    op.drop_index("ix_draft_sessions_user_status", table_name="draft_sessions")
    op.drop_index("ix_draft_sessions_expires_at", table_name="draft_sessions")
    op.drop_table("draft_sessions")
