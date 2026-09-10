"""Add workspace change work item event.

Revision ID: 0027_work_item_workspace_move
Revises: 0026_overview_drag_drop
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027_work_item_workspace_move"
down_revision: str | None = "0026_overview_drag_drop"
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
    "workspace_changed",
)


def _quoted(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(repr(value) for value in values) + ")"


def upgrade() -> None:
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
