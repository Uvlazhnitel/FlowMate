"""Remove the retired Meeting Mode schema.

Revision ID: 0024_remove_meeting_mode
Revises: 0023_planner_manual_queue
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_remove_meeting_mode"
down_revision: str | None = "0023_planner_manual_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEETING_TABLES = (
    "meeting_agenda_entries",
    "meeting_work_items",
    "meeting_review_items",
    "meeting_reviews",
    "meeting_setup_sessions",
    "meeting_events",
    "meeting_notes",
    "meeting_topics",
    "meeting_participants",
    "meetings",
)


def _guard_against_data_loss() -> None:
    bind = op.get_bind()
    lock_targets = ", ".join((*MEETING_TABLES, "draft_sessions", "ai_processing_jobs"))
    bind.execute(sa.text(f"LOCK TABLE {lock_targets} IN ACCESS EXCLUSIVE MODE"))

    populated: list[tuple[str, int]] = []
    for table_name in MEETING_TABLES:
        count = bind.scalar(sa.text(f"SELECT count(*) FROM {table_name}"))
        if count:
            populated.append((table_name, int(count)))

    draft_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM draft_sessions "
            "WHERE meeting_id IS NOT NULL "
            "OR capture_sequence IS NOT NULL "
            "OR capture_review_status IS NOT NULL "
            "OR capture_context <> '{}'::jsonb "
            "OR overall_confidence IS NOT NULL"
        )
    )
    if draft_count:
        populated.append(("draft_sessions (Meeting fields)", int(draft_count)))

    job_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM ai_processing_jobs "
            "WHERE job_kind IN ('meeting_capture_parse', 'meeting_review_generate')"
        )
    )
    if job_count:
        populated.append(("ai_processing_jobs (Meeting kinds)", int(job_count)))

    if populated:
        details = ", ".join(f"{name}={count}" for name, count in populated)
        raise RuntimeError(
            "Cannot remove Meeting Mode while legacy data exists: "
            f"{details}. Export or archive these records, verify the archive, "
            "and consciously remove them before running the migration again."
        )


def upgrade() -> None:
    _guard_against_data_loss()

    for table_name in MEETING_TABLES[:-1]:
        op.drop_table(table_name)

    op.drop_index("uq_draft_sessions_user_open", table_name="draft_sessions")
    op.drop_index("ix_draft_sessions_meeting_capture", table_name="draft_sessions")
    op.drop_constraint(
        "ck_draft_sessions_capture_fields", "draft_sessions", type_="check"
    )
    op.drop_constraint(
        "ck_draft_sessions_overall_confidence", "draft_sessions", type_="check"
    )
    op.drop_constraint(
        "ck_draft_sessions_capture_review_status", "draft_sessions", type_="check"
    )
    op.drop_constraint(
        "ck_draft_sessions_capture_sequence_positive",
        "draft_sessions",
        type_="check",
    )
    op.drop_constraint(
        "uq_draft_sessions_meeting_capture_sequence",
        "draft_sessions",
        type_="unique",
    )
    op.drop_constraint(
        "fk_draft_sessions_meeting_id", "draft_sessions", type_="foreignkey"
    )
    op.drop_column("draft_sessions", "overall_confidence")
    op.drop_column("draft_sessions", "capture_context")
    op.drop_column("draft_sessions", "capture_review_status")
    op.drop_column("draft_sessions", "capture_sequence")
    op.drop_column("draft_sessions", "meeting_id")
    op.create_index(
        "uq_draft_sessions_user_open",
        "draft_sessions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('parsing', 'needs_clarification', 'ready')"
        ),
    )

    op.drop_table("meetings")

    op.drop_constraint(
        "ck_ai_processing_jobs_kind", "ai_processing_jobs", type_="check"
    )
    op.create_check_constraint(
        "ck_ai_processing_jobs_kind",
        "ai_processing_jobs",
        "job_kind IN ('draft_parse','draft_refine')",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Migration 0024_remove_meeting_mode is irreversible: the removed schema "
        "can be recreated, but deleted Meeting Mode data cannot be restored "
        "automatically."
    )
