"""Add search action sessions for Telegram navigation.

Revision ID: 0012_main_menu_navigation
Revises: 0011_notification_preferences
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_main_menu_navigation"
down_revision: str | None = "0011_notification_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_work_item_action_sessions_action",
        "work_item_action_sessions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_work_item_action_sessions_action",
        "work_item_action_sessions",
        "action IN ('select_record', 'reschedule', 'add_note', 'change_topic', "
        "'add_person', 'replace_person', 'reminder_snooze', 'digest_review', "
        "'search')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    search_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM work_item_action_sessions WHERE action = 'search'"
        )
    )
    if search_count:
        raise RuntimeError("Cannot downgrade while search sessions exist")
    op.drop_constraint(
        "ck_work_item_action_sessions_action",
        "work_item_action_sessions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_work_item_action_sessions_action",
        "work_item_action_sessions",
        "action IN ('select_record', 'reschedule', 'add_note', 'change_topic', "
        "'add_person', 'replace_person', 'reminder_snooze', 'digest_review')",
    )
