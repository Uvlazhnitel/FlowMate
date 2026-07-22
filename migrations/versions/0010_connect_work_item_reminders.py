"""Connect WorkItem dates to managed reminders.

Revision ID: 0010_connect_work_item_reminders
Revises: 0009_create_reminders
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_connect_work_item_reminders"
down_revision: str | None = "0009_create_reminders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _epoch_microseconds(value: datetime) -> int:
    normalized = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = normalized - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _backfill_exact_reminders() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, user_id, type, due_at, next_follow_up_at "
            "FROM work_items WHERE status IN "
            "('inbox', 'planned', 'active', 'waiting', 'snoozed')"
        )
    ).mappings()
    reminders = sa.table(
        "reminders",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("work_item_id", sa.Uuid()),
        sa.column("type", sa.String()),
        sa.column("scheduled_at", sa.DateTime(timezone=True)),
        sa.column("reference_at", sa.DateTime(timezone=True)),
        sa.column("schedule_kind", sa.String()),
        sa.column("status", sa.String()),
        sa.column("deduplication_key", sa.String()),
    )
    values: list[dict[str, object]] = []
    for row in rows:
        item_type = row["type"]
        if item_type == "follow_up":
            reminder_type = "follow_up"
            reference_at = row["next_follow_up_at"]
        elif item_type == "waiting":
            reminder_type = "waiting"
            reference_at = row["due_at"]
        else:
            reminder_type = "deadline"
            reference_at = row["due_at"]
        if reference_at is None:
            continue
        key = (
            f"work-item:{row['id']}:{reminder_type}:exact:"
            f"{_epoch_microseconds(reference_at)}"
        )
        values.append(
            {
                "id": uuid4(),
                "user_id": row["user_id"],
                "work_item_id": row["id"],
                "type": reminder_type,
                "scheduled_at": reference_at,
                "reference_at": reference_at,
                "schedule_kind": "exact",
                "status": "pending",
                "deduplication_key": key,
            }
        )
    if values:
        statement = postgresql.insert(reminders).values(values)
        bind.execute(
            statement.on_conflict_do_nothing(
                index_elements=["user_id", "deduplication_key"]
            )
        )


def upgrade() -> None:
    op.add_column(
        "reminders",
        sa.Column("reference_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reminders",
        sa.Column(
            "schedule_kind",
            sa.String(length=24),
            server_default="manual",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_reminders_schedule_kind",
        "reminders",
        "schedule_kind IN ('manual', 'exact', 'before_deadline', 'snooze')",
    )
    op.create_check_constraint(
        "ck_reminders_managed_reference",
        "reminders",
        "schedule_kind = 'manual' OR reference_at IS NOT NULL",
    )
    op.create_index(
        "ix_reminders_work_item_status_kind",
        "reminders",
        ["work_item_id", "status", "schedule_kind"],
    )

    op.drop_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        "event_type IN ('created', 'updated', 'status_changed', 'linked', "
        "'completed', 'reopened', 'cancelled', 'rescheduled', 'note_added', "
        "'topic_changed', 'person_changed', 'waiting_received', "
        "'person_replied', 'reminder_snoozed', 'archived')",
    )
    _backfill_exact_reminders()


def downgrade() -> None:
    bind = op.get_bind()
    new_event_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM work_item_events WHERE event_type IN "
            "('person_replied', 'reminder_snoozed', 'archived')"
        )
    )
    if new_event_count:
        raise RuntimeError("Cannot downgrade while Stage 4.2 work item events exist")
    op.drop_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        "event_type IN ('created', 'updated', 'status_changed', 'linked', "
        "'completed', 'reopened', 'cancelled', 'rescheduled', 'note_added', "
        "'topic_changed', 'person_changed', 'waiting_received')",
    )
    op.drop_index(
        "ix_reminders_work_item_status_kind",
        table_name="reminders",
    )
    op.drop_constraint(
        "ck_reminders_managed_reference",
        "reminders",
        type_="check",
    )
    op.drop_constraint(
        "ck_reminders_schedule_kind",
        "reminders",
        type_="check",
    )
    op.drop_column("reminders", "schedule_kind")
    op.drop_column("reminders", "reference_at")
