"""Add remaining Stage 6 PWA operational state.

Revision ID: 0015_pwa_remaining_screens
Revises: 0014_pwa_operations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_pwa_remaining_screens"
down_revision: str | None = "0014_pwa_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_EVENT_TYPES = (
    "created",
    "updated",
    "status_changed",
    "linked",
    "completed",
    "reopened",
    "cancelled",
    "rescheduled",
    "note_added",
    "topic_changed",
    "person_changed",
    "waiting_received",
    "person_replied",
    "reminder_snoozed",
    "archived",
)
PLANNER_STATUSES = (
    "not_required",
    "needs_transfer",
    "transferred",
    "update_required",
    "no_longer_relevant",
)


def _quoted(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(repr(value) for value in values) + ")"


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column(
            "planner_status",
            sa.String(length=32),
            server_default="not_required",
            nullable=False,
        ),
    )
    op.add_column(
        "work_items",
        sa.Column("planner_transferred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_work_items_planner_status",
        "work_items",
        f"planner_status IN {_quoted(PLANNER_STATUSES)}",
    )
    op.create_index(
        "ix_work_items_user_planner",
        "work_items",
        ["user_id", "planner_status"],
    )
    op.execute(
        """
        UPDATE work_items
        SET planner_status = CASE
            WHEN status IN ('inbox', 'planned', 'active', 'waiting', 'snoozed')
                THEN 'needs_transfer'
            ELSE 'no_longer_relevant'
        END
        WHERE type IN ('task', 'follow_up', 'waiting')
        """
    )

    op.drop_constraint("ck_work_item_events_type", "work_item_events", type_="check")
    op.create_check_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        f"event_type IN {_quoted((*OLD_EVENT_TYPES, 'planner_status_changed'))}",
    )

    op.add_column(
        "notes",
        sa.Column(
            "inbox_disposition",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_notes_inbox_disposition",
        "notes",
        "inbox_disposition IN ('pending', 'kept', 'archived')",
    )
    op.create_index(
        "ix_notes_user_inbox_disposition",
        "notes",
        ["user_id", "inbox_disposition", "created_at"],
    )

    op.add_column(
        "user_notification_preferences",
        sa.Column(
            "date_display_format",
            sa.String(length=32),
            server_default="day_month_year",
            nullable=False,
        ),
    )
    op.add_column(
        "user_notification_preferences",
        sa.Column(
            "time_display_format",
            sa.String(length=8),
            server_default="24h",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_user_notification_preferences_date_format",
        "user_notification_preferences",
        "date_display_format IN ('day_month_year', 'year_month_day')",
    )
    op.create_check_constraint(
        "ck_user_notification_preferences_time_format",
        "user_notification_preferences",
        "time_display_format IN ('24h', '12h')",
    )

    op.add_column(
        "draft_items", sa.Column("selected_topic_id", sa.Uuid(), nullable=True)
    )
    op.add_column(
        "draft_items",
        sa.Column(
            "selected_priority",
            sa.String(length=16),
            server_default="normal",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_draft_items_selected_priority",
        "draft_items",
        "selected_priority IN ('low', 'normal', 'high', 'urgent')",
    )
    op.create_foreign_key(
        "fk_draft_items_selected_topic_id_topics",
        "draft_items",
        "topics",
        ["selected_topic_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "draft_item_people",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("draft_item_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["draft_item_id"], ["draft_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_item_id", "person_id", name="uq_draft_item_people_item_person"
        ),
    )
    op.create_index("ix_draft_item_people_user_id", "draft_item_people", ["user_id"])
    op.create_index(
        "ix_draft_item_people_person_id", "draft_item_people", ["person_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_draft_item_people_person_id", table_name="draft_item_people")
    op.drop_index("ix_draft_item_people_user_id", table_name="draft_item_people")
    op.drop_table("draft_item_people")
    op.drop_constraint(
        "fk_draft_items_selected_topic_id_topics", "draft_items", type_="foreignkey"
    )
    op.drop_constraint("ck_draft_items_selected_priority", "draft_items", type_="check")
    op.drop_column("draft_items", "selected_priority")
    op.drop_column("draft_items", "selected_topic_id")

    op.drop_constraint(
        "ck_user_notification_preferences_time_format",
        "user_notification_preferences",
        type_="check",
    )
    op.drop_constraint(
        "ck_user_notification_preferences_date_format",
        "user_notification_preferences",
        type_="check",
    )
    op.drop_column("user_notification_preferences", "time_display_format")
    op.drop_column("user_notification_preferences", "date_display_format")

    op.drop_index("ix_notes_user_inbox_disposition", table_name="notes")
    op.drop_constraint("ck_notes_inbox_disposition", "notes", type_="check")
    op.drop_column("notes", "inbox_disposition")

    op.drop_constraint("ck_work_item_events_type", "work_item_events", type_="check")
    op.execute(
        "UPDATE work_item_events SET event_type = 'updated' "
        "WHERE event_type = 'planner_status_changed'"
    )
    op.create_check_constraint(
        "ck_work_item_events_type",
        "work_item_events",
        f"event_type IN {_quoted(OLD_EVENT_TYPES)}",
    )
    op.drop_index("ix_work_items_user_planner", table_name="work_items")
    op.drop_constraint("ck_work_items_planner_status", "work_items", type_="check")
    op.drop_column("work_items", "planner_transferred_at")
    op.drop_column("work_items", "planner_status")
