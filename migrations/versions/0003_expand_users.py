"""Expand the Stage 0 users table.

Revision ID: 0003_expand_users
Revises: 0002_create_users
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_expand_users"
down_revision: str | None = "0002_create_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "users",
        "telegram_user_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.add_column(
        "users", sa.Column("display_name", sa.String(length=255), nullable=True)
    )
    op.create_check_constraint(
        "ck_users_telegram_user_id_positive",
        "users",
        "telegram_user_id IS NULL OR telegram_user_id > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_telegram_user_id_positive", "users", type_="check")
    op.drop_column("users", "display_name")
    op.alter_column(
        "users",
        "telegram_user_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
