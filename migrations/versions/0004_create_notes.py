"""Create the notes table.

Revision ID: 0004_create_notes
Revises: 0003_expand_users
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_create_notes"
down_revision: str | None = "0003_expand_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("telegram_update_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(content)) > 0",
            name="ck_notes_content_not_blank",
        ),
        sa.CheckConstraint(
            "source IN ('text', 'voice')",
            name="ck_notes_source",
        ),
        sa.CheckConstraint(
            "telegram_update_id > 0",
            name="ck_notes_telegram_update_id_positive",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_notes_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notes"),
        sa.UniqueConstraint(
            "telegram_update_id",
            name="notes_telegram_update_id_key",
        ),
    )
    op.create_index(
        "ix_notes_user_id_created_at",
        "notes",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notes_user_id_created_at", table_name="notes")
    op.drop_table("notes")
