"""Persist Overview task ordering.

Revision ID: 0029_work_item_sort_rank
Revises: 0028_work_item_subtasks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_work_item_sort_rank"
down_revision: str | None = "0028_work_item_subtasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("work_items", sa.Column("sort_rank", sa.BigInteger(), nullable=True))
    op.create_index(
        "ix_work_items_user_status_sort_rank",
        "work_items",
        ["user_id", "status", "sort_rank", "id"],
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id, row_number() OVER (
                    PARTITION BY user_id ORDER BY updated_at DESC, id
                ) * 1000 AS rank
                FROM work_items
                WHERE status NOT IN ('done', 'cancelled', 'archived')
            )
            UPDATE work_items SET sort_rank = ranked.rank
            FROM ranked WHERE work_items.id = ranked.id
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_work_items_user_status_sort_rank", table_name="work_items")
    op.drop_column("work_items", "sort_rank")
