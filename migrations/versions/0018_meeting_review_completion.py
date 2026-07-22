"""Add meeting review and completion workflow.

Revision ID: 0018_meeting_review_completion
Revises: 0017_meeting_fast_capture
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_meeting_review_completion"
down_revision: str | None = "0017_meeting_fast_capture"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_meeting_events_type", "meeting_events", type_="check")
    op.create_check_constraint(
        "ck_meeting_events_type",
        "meeting_events",
        "event_type IN ('created','started','ended','cancelled','review_generated',"
        "'review_failed','clarification_answered','converted','completed',"
        "'agenda_updated')",
    )
    op.create_table(
        "meeting_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column(
            "suggested_next_actions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("current_item_id", sa.Uuid()),
        sa.Column("current_question", sa.Text()),
        sa.Column("current_question_context", postgresql.JSONB()),
        sa.Column("current_question_message_id", sa.BigInteger()),
        sa.Column(
            "processed_update_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "generation_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
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
            "status IN ('processing','review_required','completed','failed')",
            name="ck_meeting_reviews_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("meeting_id", name="uq_meeting_reviews_meeting"),
    )
    op.create_index(
        "ix_meeting_reviews_user_status", "meeting_reviews", ["user_id", "status"]
    )
    op.create_table(
        "meeting_review_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("source_capture_id", sa.Uuid()),
        sa.Column("source_draft_item_id", sa.Uuid()),
        sa.Column("source_work_item_id", sa.Uuid()),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("origin", sa.String(24), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("suggested_next_action", sa.Text()),
        sa.Column(
            "consequences",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "related_positions",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("clarification_question", sa.Text()),
        sa.Column("clarification_answer", sa.Text()),
        sa.Column(
            "planner_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("result_work_item_id", sa.Uuid()),
        sa.Column("result_note_id", sa.Uuid()),
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
            "origin IN ('capture','existing_agenda','manual_review')",
            name="ck_meeting_review_items_origin",
        ),
        sa.CheckConstraint(
            "category IN ('task','follow_up','waiting','answered_question',"
            "'unresolved_question','note','decision','agenda_item')",
            name="ck_meeting_review_items_category",
        ),
        sa.CheckConstraint(
            "status IN ('pending','ready','clarification_required','excluded',"
            "'inbox','converted')",
            name="ck_meeting_review_items_status",
        ),
        sa.CheckConstraint(
            "origin != 'capture' OR source_capture_id IS NOT NULL",
            name="ck_meeting_review_items_capture_source",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["review_id"], ["meeting_reviews.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_capture_id"], ["draft_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_item_id"], ["draft_items.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_work_item_id"], ["work_items.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["result_work_item_id"], ["work_items.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["result_note_id"], ["notes.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "review_id", "position", name="uq_meeting_review_items_position"
        ),
    )
    op.create_index(
        "ix_meeting_review_items_review_status",
        "meeting_review_items",
        ["review_id", "status"],
    )
    op.create_table(
        "meeting_work_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("review_item_id", sa.Uuid()),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role IN ('result','decision','agenda')", name="ck_meeting_work_items_role"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["review_item_id"], ["meeting_review_items.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("meeting_id", "work_item_id", name="uq_meeting_work_items"),
    )
    op.create_index(
        "ix_meeting_work_items_work_item", "meeting_work_items", ["work_item_id"]
    )
    op.create_table(
        "meeting_agenda_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("meeting_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("result", sa.Text()),
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
            "outcome IN ('pending','discussed','answered','deferred','unresolved')",
            name="ck_meeting_agenda_entries_outcome",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "meeting_id", "work_item_id", name="uq_meeting_agenda_entries"
        ),
    )
    op.create_index(
        "ix_meeting_agenda_entries_meeting",
        "meeting_agenda_entries",
        ["meeting_id", "outcome"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_meeting_agenda_entries_meeting", table_name="meeting_agenda_entries"
    )
    op.drop_table("meeting_agenda_entries")
    op.drop_index("ix_meeting_work_items_work_item", table_name="meeting_work_items")
    op.drop_table("meeting_work_items")
    op.drop_index(
        "ix_meeting_review_items_review_status", table_name="meeting_review_items"
    )
    op.drop_table("meeting_review_items")
    op.drop_index("ix_meeting_reviews_user_status", table_name="meeting_reviews")
    op.drop_table("meeting_reviews")
    op.drop_constraint("ck_meeting_events_type", "meeting_events", type_="check")
    op.create_check_constraint(
        "ck_meeting_events_type",
        "meeting_events",
        "event_type IN ('created','started','ended','cancelled')",
    )
