"""Add draft conversion provenance to notes.

Revision ID: 0007_draft_conversion
Revises: 0006_create_task_engine
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_draft_conversion"
down_revision: str | None = "0006_create_task_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column("source_draft_item_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_notes_source_draft_item_id_draft_items",
        "notes",
        "draft_items",
        ["source_draft_item_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint(
        "uq_notes_source_draft_item_id",
        "notes",
        ["source_draft_item_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_notes_source_draft_item_id",
        "notes",
        type_="unique",
    )
    op.drop_constraint(
        "fk_notes_source_draft_item_id_draft_items",
        "notes",
        type_="foreignkey",
    )
    op.drop_column("notes", "source_draft_item_id")
