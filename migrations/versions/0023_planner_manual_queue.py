"""Make Planner queue opt-in.

Revision ID: 0023_planner_manual_queue
Revises: 0022_fast_capture_defaults
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023_planner_manual_queue"
down_revision: str | None = "0022_fast_capture_defaults"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE work_items AS item
        SET planner_status = 'not_required'
        WHERE item.planner_status = 'needs_transfer'
          AND item.planner_transferred_at IS NULL
          AND NOT EXISTS (
              SELECT 1
              FROM work_item_events AS event
              WHERE event.work_item_id = item.id
                AND event.event_type = 'planner_status_changed'
                AND event.payload ->> 'new' = 'needs_transfer'
                AND NOT event.payload ? 'reason'
          )
        """
    )


def downgrade() -> None:
    # The previous automatic state cannot be reconstructed without affecting
    # records intentionally excluded from Planner after this migration.
    pass
