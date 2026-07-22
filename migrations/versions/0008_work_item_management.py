"""Add Telegram work item management state and event idempotency.

Revision ID: 0008_work_item_management
Revises: 0007_draft_conversion
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_work_item_management"
down_revision: str | None = "0007_draft_conversion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        type_="check",
    )
    op.add_column(
        "work_item_events",
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        "event_type IN ('created', 'updated', 'status_changed', 'linked', "
        "'completed', 'reopened', 'cancelled', 'rescheduled', 'note_added', "
        "'topic_changed', 'person_changed', 'waiting_received')",
    )
    op.create_check_constraint(
        "ck_work_item_events_telegram_update_id_positive",
        "work_item_events",
        "telegram_update_id IS NULL OR telegram_update_id > 0",
    )
    op.create_unique_constraint(
        "uq_work_item_events_telegram_update_id",
        "work_item_events",
        ["telegram_update_id"],
    )

    op.create_table(
        "work_item_action_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="open", nullable=False
        ),
        sa.Column(
            "context",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("prompt_message_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=True),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('select_record', 'reschedule', 'add_note', "
            "'change_topic', 'add_person', 'replace_person')",
            name="ck_work_item_action_sessions_action",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_work_item_action_sessions_expiration",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'completed', 'cancelled', 'expired')",
            name="ck_work_item_action_sessions_status",
        ),
        sa.CheckConstraint(
            "telegram_update_id IS NULL OR telegram_update_id > 0",
            name="ck_work_item_action_sessions_telegram_update_id_positive",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_work_item_action_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_work_item_action_sessions_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_item_action_sessions"),
        sa.UniqueConstraint(
            "telegram_update_id",
            name="uq_work_item_action_sessions_telegram_update_id",
        ),
    )
    op.create_index(
        "ix_work_item_action_sessions_expires_at",
        "work_item_action_sessions",
        ["expires_at"],
    )
    op.create_index(
        "uq_work_item_action_sessions_user_open",
        "work_item_action_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_work_item_action_sessions_user_open",
        table_name="work_item_action_sessions",
    )
    op.drop_index(
        "ix_work_item_action_sessions_expires_at",
        table_name="work_item_action_sessions",
    )
    op.drop_table("work_item_action_sessions")
    op.drop_constraint(
        "uq_work_item_events_telegram_update_id",
        "work_item_events",
        type_="unique",
    )
    op.drop_constraint(
        "ck_work_item_events_telegram_update_id_positive",
        "work_item_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        type_="check",
    )
    op.drop_column("work_item_events", "telegram_update_id")
    op.create_check_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        "event_type IN ('created', 'updated', 'status_changed', 'linked')",
    )
