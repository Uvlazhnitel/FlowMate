"""Add PWA login codes and server-side sessions.

Revision ID: 0013_pwa_auth
Revises: 0012_main_menu_navigation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_pwa_auth"
down_revision: str | None = "0012_main_menu_navigation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pwa_login_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_pwa_login_codes_attempt_count_non_negative",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pwa_login_codes_user_created",
        "pwa_login_codes",
        ["user_id", "created_at"],
    )
    op.create_table(
        "pwa_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("csrf_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index("ix_pwa_sessions_expires", "pwa_sessions", ["expires_at"])
    op.create_index(
        "ix_pwa_sessions_user_expires",
        "pwa_sessions",
        ["user_id", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pwa_sessions_user_expires", table_name="pwa_sessions")
    op.drop_index("ix_pwa_sessions_expires", table_name="pwa_sessions")
    op.drop_table("pwa_sessions")
    op.drop_index("ix_pwa_login_codes_user_created", table_name="pwa_login_codes")
    op.drop_table("pwa_login_codes")
