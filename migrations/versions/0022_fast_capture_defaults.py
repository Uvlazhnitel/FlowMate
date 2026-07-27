"""Add quick capture action and default reminder time.

Revision ID: 0022_fast_capture_defaults
Revises: 0021_workspace_separation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_fast_capture_defaults"
down_revision: str | None = "0021_workspace_separation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIONS = (
    "'capture_new', 'select_record', 'reschedule', 'add_note', 'change_topic', "
    "'add_person', 'replace_person', 'reminder_snooze', 'digest_review', 'search'"
)
_OLD_ACTIONS = (
    "'select_record', 'reschedule', 'add_note', 'change_topic', 'add_person', "
    "'replace_person', 'reminder_snooze', 'digest_review', 'search'"
)


def upgrade() -> None:
    op.add_column(
        "user_notification_preferences",
        sa.Column(
            "default_reminder_time",
            sa.Time(),
            nullable=False,
            server_default=sa.text("'09:00:00'::time"),
        ),
    )
    op.drop_constraint(
        "ck_work_item_action_sessions_action",
        "work_item_action_sessions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_work_item_action_sessions_action",
        "work_item_action_sessions",
        f"action IN ({_ACTIONS})",
    )


def downgrade() -> None:
    bind = op.get_bind()
    capture_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM work_item_action_sessions "
            "WHERE action = 'capture_new'"
        )
    )
    if capture_count:
        raise RuntimeError("Cannot downgrade while capture sessions exist")
    op.drop_constraint(
        "ck_work_item_action_sessions_action",
        "work_item_action_sessions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_work_item_action_sessions_action",
        "work_item_action_sessions",
        f"action IN ({_OLD_ACTIONS})",
    )
    op.drop_column("user_notification_preferences", "default_reminder_time")
