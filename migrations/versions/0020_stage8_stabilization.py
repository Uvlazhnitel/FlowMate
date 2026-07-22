"""Add Stage 8 stabilization persistence.

Revision ID: 0020_stage8_stabilization
Revises: 0019_stage7_stabilization
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_stage8_stabilization"
down_revision: str | None = "0019_stage7_stabilization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_notes_content_not_blank", "notes", type_="check")
    op.alter_column("notes", "content", existing_type=sa.Text(), nullable=True)
    op.add_column(
        "notes", sa.Column("transcript_redacted_at", sa.DateTime(timezone=True))
    )
    op.create_check_constraint(
        "ck_notes_content_or_redacted",
        "notes",
        "(content IS NOT NULL AND char_length(btrim(content)) > 0 AND "
        "transcript_redacted_at IS NULL) OR (content IS NULL AND source = 'voice' "
        "AND transcript_redacted_at IS NOT NULL)",
    )
    op.add_column(
        "draft_sessions",
        sa.Column(
            "prompt_version", sa.String(32), nullable=False, server_default="legacy-v1"
        ),
    )
    op.add_column(
        "draft_sessions",
        sa.Column("processing_started_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "meeting_reviews",
        sa.Column(
            "prompt_version", sa.String(32), nullable=False, server_default="legacy-v1"
        ),
    )
    op.drop_constraint("ck_reminders_status", "reminders", type_="check")
    op.create_check_constraint(
        "ck_reminders_status",
        "reminders",
        "status IN ('pending','processing','sent','snoozed','cancelled','failed',"
        "'delivery_unknown')",
    )
    op.add_column(
        "reminders", sa.Column("delivery_started_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "reminders", sa.Column("delivery_unknown_at", sa.DateTime(timezone=True))
    )

    op.create_table(
        "telegram_operation_receipts",
        sa.Column("update_id", sa.BigInteger(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger()),
        sa.Column("event_kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('processing','completed','retryable_failed')",
            name="ck_telegram_operation_receipts_status",
        ),
        sa.CheckConstraint(
            "attempt_count > 0", name="ck_telegram_operation_receipts_attempts"
        ),
    )
    op.create_index(
        "ix_telegram_receipts_status_lease",
        "telegram_operation_receipts",
        ["status", "lease_expires_at"],
    )
    op.create_table(
        "ai_processing_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("job_kind", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("operation_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("prompt_name", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("input_text", sa.Text()),
        sa.Column("input_source", sa.String(16)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "job_kind IN ('draft_parse','draft_refine','meeting_capture_parse',"
            "'meeting_review_generate')",
            name="ck_ai_processing_jobs_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending','processing','completed','failed')",
            name="ck_ai_processing_jobs_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_ai_processing_jobs_attempts"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "job_kind", "entity_id", "operation_key", name="uq_ai_processing_job"
        ),
    )
    op.create_index(
        "ix_ai_processing_jobs_due", "ai_processing_jobs", ["status", "next_attempt_at"]
    )
    op.create_index(
        "ix_ai_processing_jobs_lease",
        "ai_processing_jobs",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_ai_processing_jobs_user", "ai_processing_jobs", ["user_id", "created_at"]
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_kind", sa.String(32)),
        sa.Column("entity_id", sa.Uuid()),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("correlation_id", sa.String(128)),
        sa.Column(
            "safe_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint(
            "actor_kind IN ('telegram','pwa','system','operator')",
            name="ck_audit_events_actor_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('success','rejected','failed','recovered')",
            name="ck_audit_events_outcome",
        ),
    )
    op.create_index(
        "ix_audit_events_user_created", "audit_events", ["user_id", "created_at", "id"]
    )
    op.create_index(
        "ix_audit_events_action_created", "audit_events", ["action", "created_at"]
    )
    op.execute(
        """
        CREATE FUNCTION flowmate_audit_events_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_append_only
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION flowmate_audit_events_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_append_only ON audit_events")
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION IF EXISTS flowmate_audit_events_append_only()")
    op.drop_table("ai_processing_jobs")
    op.drop_table("telegram_operation_receipts")
    op.drop_column("reminders", "delivery_unknown_at")
    op.drop_column("reminders", "delivery_started_at")
    op.drop_constraint("ck_reminders_status", "reminders", type_="check")
    op.execute(
        "UPDATE reminders SET status = 'failed' WHERE status = 'delivery_unknown'"
    )
    op.create_check_constraint(
        "ck_reminders_status",
        "reminders",
        "status IN ('pending','processing','sent','snoozed','cancelled','failed')",
    )
    op.drop_column("meeting_reviews", "prompt_version")
    op.drop_column("draft_sessions", "processing_started_at")
    op.drop_column("draft_sessions", "prompt_version")
    op.drop_constraint("ck_notes_content_or_redacted", "notes", type_="check")
    op.execute(
        "UPDATE notes SET content = '[voice transcript removed]' WHERE content IS NULL"
    )
    op.drop_column("notes", "transcript_redacted_at")
    op.alter_column("notes", "content", existing_type=sa.Text(), nullable=False)
    op.create_check_constraint(
        "ck_notes_content_not_blank", "notes", "char_length(btrim(content)) > 0"
    )
