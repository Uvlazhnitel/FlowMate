"""Add user notification preferences and digest scheduling metadata.

Revision ID: 0011_notification_preferences
Revises: 0010_connect_work_item_reminders
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_notification_preferences"
down_revision: str | None = "0010_connect_work_item_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_notification_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "morning_digest_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("morning_digest_time", sa.Time(), nullable=False),
        sa.Column(
            "evening_digest_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("evening_digest_time", sa.Time(), nullable=False),
        sa.Column(
            "quiet_hours_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("quiet_hours_start", sa.Time(), nullable=False),
        sa.Column("quiet_hours_end", sa.Time(), nullable=False),
        sa.Column("default_snooze_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "send_empty_digests",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "char_length(btrim(timezone)) > 0",
            name="ck_user_notification_preferences_timezone_not_blank",
        ),
        sa.CheckConstraint(
            "default_snooze_minutes BETWEEN 1 AND 10080",
            name="ck_user_notification_preferences_snooze_range",
        ),
        sa.CheckConstraint(
            "quiet_hours_start <> quiet_hours_end",
            name="ck_user_notification_preferences_quiet_range",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_notification_preferences_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_notification_preferences"),
    )
    op.add_column("reminders", sa.Column("digest_local_date", sa.Date()))
    op.add_column("reminders", sa.Column("schedule_timezone", sa.String(length=64)))
    op.create_unique_constraint(
        "uq_reminders_user_digest_local_date",
        "reminders",
        ["user_id", "type", "digest_local_date"],
    )
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


def downgrade() -> None:
    op.drop_constraint(
        "ck_work_item_action_sessions_action",
        "work_item_action_sessions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_work_item_action_sessions_action",
        "work_item_action_sessions",
        "action IN ('select_record', 'reschedule', 'add_note', 'change_topic', "
        "'add_person', 'replace_person')",
    )
    op.drop_constraint(
        "uq_reminders_user_digest_local_date", "reminders", type_="unique"
    )
    op.drop_column("reminders", "schedule_timezone")
    op.drop_column("reminders", "digest_local_date")
    op.drop_table("user_notification_preferences")
