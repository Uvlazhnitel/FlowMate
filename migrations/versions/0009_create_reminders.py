"""Create persistent reminders.

Revision ID: 0009_create_reminders
Revises: 0008_work_item_management
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_create_reminders"
down_revision: str | None = "0008_work_item_management"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_token", sa.Uuid(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "delivery_attempts", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("deduplication_key", sa.String(length=255), nullable=False),
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
            "delivery_attempts >= 0",
            name="ck_reminders_delivery_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "char_length(btrim(deduplication_key)) > 0",
            name="ck_reminders_deduplication_key_not_blank",
        ),
        sa.CheckConstraint(
            "message IS NULL OR char_length(btrim(message)) > 0",
            name="ck_reminders_message_not_blank",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'snoozed', "
            "'cancelled', 'failed')",
            name="ck_reminders_status",
        ),
        sa.CheckConstraint(
            "type IN ('deadline', 'follow_up', 'waiting', 'morning_digest', "
            "'evening_digest', 'custom')",
            name="ck_reminders_type",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_reminders_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_reminders_work_item_id_work_items",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reminders"),
        sa.UniqueConstraint(
            "user_id",
            "deduplication_key",
            name="uq_reminders_user_deduplication_key",
        ),
    )
    op.create_index(
        "ix_reminders_status_scheduled_at",
        "reminders",
        ["status", "scheduled_at"],
    )
    op.create_index(
        "ix_reminders_user_status",
        "reminders",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_reminders_work_item_id",
        "reminders",
        ["work_item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_reminders_work_item_id", table_name="reminders")
    op.drop_index("ix_reminders_user_status", table_name="reminders")
    op.drop_index("ix_reminders_status_scheduled_at", table_name="reminders")
    op.drop_table("reminders")
