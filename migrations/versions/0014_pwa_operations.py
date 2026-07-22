"""Add PWA work-item action idempotency.

Revision ID: 0014_pwa_operations
Revises: 0013_pwa_auth
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_pwa_operations"
down_revision: str | None = "0013_pwa_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "work_item_events",
        sa.Column("client_action_id", sa.Uuid(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_work_item_events_user_client_action_id",
        "work_item_events",
        ["user_id", "client_action_id"],
    )
    op.create_check_constraint(
        "ck_work_item_events_one_action_origin",
        "work_item_events",
        "num_nonnulls(telegram_update_id, client_action_id) <= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_work_item_events_one_action_origin",
        "work_item_events",
        type_="check",
    )
    op.drop_constraint(
        "uq_work_item_events_user_client_action_id",
        "work_item_events",
        type_="unique",
    )
    op.drop_column("work_item_events", "client_action_id")
