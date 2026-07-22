"""Add fast Meeting Mode captures.

Revision ID: 0017_meeting_fast_capture
Revises: 0016_meeting_mode_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_meeting_fast_capture"
down_revision: str | None = "0016_meeting_mode_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_draft_sessions_user_open", table_name="draft_sessions")
    op.add_column("draft_sessions", sa.Column("meeting_id", sa.Uuid()))
    op.add_column("draft_sessions", sa.Column("capture_sequence", sa.Integer()))
    op.add_column("draft_sessions", sa.Column("capture_review_status", sa.String(16)))
    op.add_column(
        "draft_sessions",
        sa.Column(
            "capture_context",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column("draft_sessions", sa.Column("overall_confidence", sa.Float()))
    op.create_foreign_key(
        "fk_draft_sessions_meeting_id",
        "draft_sessions",
        "meetings",
        ["meeting_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_draft_sessions_meeting_capture_sequence",
        "draft_sessions",
        ["meeting_id", "capture_sequence"],
    )
    op.create_check_constraint(
        "ck_draft_sessions_capture_sequence_positive",
        "draft_sessions",
        "capture_sequence IS NULL OR capture_sequence > 0",
    )
    op.create_check_constraint(
        "ck_draft_sessions_capture_review_status",
        "draft_sessions",
        "capture_review_status IS NULL OR capture_review_status IN "
        "('pending','edited','removed')",
    )
    op.create_check_constraint(
        "ck_draft_sessions_overall_confidence",
        "draft_sessions",
        "overall_confidence IS NULL OR "
        "(overall_confidence >= 0 AND overall_confidence <= 1)",
    )
    op.create_check_constraint(
        "ck_draft_sessions_capture_fields",
        "draft_sessions",
        "(meeting_id IS NULL AND capture_sequence IS NULL AND "
        "capture_review_status IS NULL) OR "
        "(meeting_id IS NOT NULL AND capture_sequence IS NOT NULL AND "
        "capture_review_status IS NOT NULL)",
    )
    op.create_index(
        "ix_draft_sessions_meeting_capture",
        "draft_sessions",
        ["meeting_id", "capture_sequence"],
    )
    op.create_index(
        "uq_draft_sessions_user_open",
        "draft_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "meeting_id IS NULL AND status IN ('parsing','needs_clarification','ready')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_draft_sessions_user_open", table_name="draft_sessions")
    op.drop_index("ix_draft_sessions_meeting_capture", table_name="draft_sessions")
    op.drop_constraint(
        "ck_draft_sessions_capture_fields", "draft_sessions", type_="check"
    )
    op.drop_constraint(
        "ck_draft_sessions_overall_confidence", "draft_sessions", type_="check"
    )
    op.drop_constraint(
        "ck_draft_sessions_capture_review_status", "draft_sessions", type_="check"
    )
    op.drop_constraint(
        "ck_draft_sessions_capture_sequence_positive", "draft_sessions", type_="check"
    )
    op.drop_constraint(
        "uq_draft_sessions_meeting_capture_sequence",
        "draft_sessions",
        type_="unique",
    )
    op.drop_constraint(
        "fk_draft_sessions_meeting_id", "draft_sessions", type_="foreignkey"
    )
    op.drop_column("draft_sessions", "overall_confidence")
    op.drop_column("draft_sessions", "capture_context")
    op.drop_column("draft_sessions", "capture_review_status")
    op.drop_column("draft_sessions", "capture_sequence")
    op.drop_column("draft_sessions", "meeting_id")
    op.create_index(
        "uq_draft_sessions_user_open",
        "draft_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('parsing','needs_clarification','ready')"),
    )
