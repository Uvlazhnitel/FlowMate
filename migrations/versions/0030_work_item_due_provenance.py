"""Store due date and time explicitness."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_work_item_due_provenance"
down_revision: str | None = "0029_work_item_sort_rank"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "work_items", sa.Column("due_date_explicit", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "work_items", sa.Column("due_time_explicit", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("work_items", "due_time_explicit")
    op.drop_column("work_items", "due_date_explicit")
