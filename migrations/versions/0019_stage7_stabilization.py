"""Index meeting events for the unified Timeline.

Revision ID: 0019_stage7_stabilization
Revises: 0018_meeting_review_completion
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_stage7_stabilization"
down_revision: str | None = "0018_meeting_review_completion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_meeting_events_user_created_id",
        "meeting_events",
        ["user_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_meeting_events_user_created_id", table_name="meeting_events")
