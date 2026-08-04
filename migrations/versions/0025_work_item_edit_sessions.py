"""Allow Telegram work-item edit sessions.

Revision ID: 0025_work_item_edit_sessions
Revises: 0024_remove_meeting_mode
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_work_item_edit_sessions"
down_revision: str | None = "0024_remove_meeting_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIONS = (
    "'capture_new', 'select_record', 'reschedule', 'add_note', 'change_topic', "
    "'add_person', 'replace_person', 'reminder_snooze', 'digest_review', "
    "'search', 'edit_field'"
)
_OLD_ACTIONS = (
    "'capture_new', 'select_record', 'reschedule', 'add_note', 'change_topic', "
    "'add_person', 'replace_person', 'reminder_snooze', 'digest_review', 'search'"
)


def upgrade() -> None:
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
    op.execute(
        sa.text("DELETE FROM work_item_action_sessions WHERE action = 'edit_field'")
    )
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
