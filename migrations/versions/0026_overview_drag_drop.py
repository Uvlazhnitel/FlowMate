"""Add overview bucket movement state.

Revision ID: 0026_overview_drag_drop
Revises: 0025_work_item_edit_sessions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_overview_drag_drop"
down_revision: str | None = "0025_work_item_edit_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_TYPES = (
    "created",
    "updated",
    "status_changed",
    "linked",
    "completed",
    "reopened",
    "cancelled",
    "rescheduled",
    "note_added",
    "topic_changed",
    "person_changed",
    "waiting_received",
    "person_replied",
    "reminder_snoozed",
    "archived",
    "planner_status_changed",
    "bucket_moved",
)


def _quoted(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(repr(value) for value in values) + ")"


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("inbox_triaged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_work_item_events_type", "work_item_events", type_="check")
    op.create_check_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        f"event_type IN {_quoted(EVENT_TYPES)}",
    )


def downgrade() -> None:
    op.drop_constraint("ck_work_item_events_type", "work_item_events", type_="check")
    op.create_check_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        f"event_type IN {_quoted(EVENT_TYPES[:-1])}",
    )
    op.drop_column("work_items", "inbox_triaged_at")
