"""Create the core Task Engine schema.

Revision ID: 0006_create_task_engine
Revises: 0005_create_drafts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_create_task_engine"
down_revision: str | None = "0005_create_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_notes_telegram_update_id_positive",
        "notes",
        type_="check",
    )
    op.drop_constraint("ck_notes_source", "notes", type_="check")
    op.alter_column("notes", "telegram_update_id", nullable=True)
    op.create_check_constraint(
        "ck_notes_source",
        "notes",
        "source IN ('text', 'voice', 'manual')",
    )
    op.create_check_constraint(
        "ck_notes_source_update_consistency",
        "notes",
        "(source IN ('text', 'voice') AND telegram_update_id > 0) OR "
        "(source = 'manual' AND telegram_update_id IS NULL)",
    )

    op.create_table(
        "topics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "aliases",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) > 0",
            name="ck_topics_name_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_topics_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_topics"),
    )
    op.create_index(
        "ix_topics_user_active",
        "topics",
        ["user_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "uq_topics_user_normalized_name",
        "topics",
        ["user_id", sa.text("lower(btrim(name))")],
        unique=True,
    )

    op.create_table(
        "people",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "aliases",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(display_name)) > 0",
            name="ck_people_display_name_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_people_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_people"),
    )
    op.create_index(
        "ix_people_user_active",
        "people",
        ["user_id", "is_active"],
        unique=False,
    )
    op.create_index(
        "ix_people_user_normalized_name",
        "people",
        ["user_id", sa.text("lower(btrim(display_name))")],
        unique=False,
    )

    op.create_table(
        "work_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="inbox",
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.String(length=16),
            server_default="normal",
            nullable=False,
        ),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_follow_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_note_id", sa.Uuid(), nullable=True),
        sa.Column("source_draft_item_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="ck_work_items_priority",
        ),
        sa.CheckConstraint(
            "status IN ('inbox', 'planned', 'active', 'waiting', 'snoozed', "
            "'done', 'cancelled', 'archived')",
            name="ck_work_items_status",
        ),
        sa.CheckConstraint(
            "char_length(btrim(title)) > 0",
            name="ck_work_items_title_not_blank",
        ),
        sa.CheckConstraint(
            "type IN ('task', 'follow_up', 'waiting', 'question', 'decision', "
            "'agenda_item')",
            name="ck_work_items_type",
        ),
        sa.ForeignKeyConstraint(
            ["source_draft_item_id"],
            ["draft_items.id"],
            name="fk_work_items_source_draft_item_id_draft_items",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_note_id"],
            ["notes.id"],
            name="fk_work_items_source_note_id_notes",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name="fk_work_items_topic_id_topics",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_work_items_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_items"),
        sa.UniqueConstraint(
            "source_draft_item_id",
            name="uq_work_items_source_draft_item_id",
        ),
    )
    op.create_index(
        "ix_work_items_source_note_id",
        "work_items",
        ["source_note_id"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_topic_id",
        "work_items",
        ["topic_id"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_user_due_at",
        "work_items",
        ["user_id", "due_at"],
        unique=False,
    )
    op.create_index(
        "ix_work_items_user_status",
        "work_items",
        ["user_id", "status"],
        unique=False,
    )

    op.create_table(
        "work_item_people",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IS NULL OR char_length(btrim(role)) > 0",
            name="ck_work_item_people_role_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name="fk_work_item_people_person_id_people",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_work_item_people_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_work_item_people_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_item_people"),
        sa.UniqueConstraint(
            "work_item_id",
            "person_id",
            name="uq_work_item_people_item_person",
        ),
    )
    op.create_index(
        "ix_work_item_people_person_id",
        "work_item_people",
        ["person_id"],
        unique=False,
    )
    op.create_index(
        "ix_work_item_people_user_id",
        "work_item_people",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "work_item_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_work_item_id", sa.Uuid(), nullable=False),
        sa.Column("target_work_item_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_work_item_id <> target_work_item_id",
            name="ck_work_item_relations_not_self",
        ),
        sa.CheckConstraint(
            "relation_type IN ('related_to', 'blocked_by', 'after_completion', "
            "'created_from', 'waiting_for')",
            name="ck_work_item_relations_type",
        ),
        sa.ForeignKeyConstraint(
            ["source_work_item_id"],
            ["work_items.id"],
            name="fk_work_item_relations_source_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_work_item_id"],
            ["work_items.id"],
            name="fk_work_item_relations_target_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_work_item_relations_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_item_relations"),
        sa.UniqueConstraint(
            "source_work_item_id",
            "target_work_item_id",
            "relation_type",
            name="uq_work_item_relations_source_target_type",
        ),
    )
    op.create_index(
        "ix_work_item_relations_user_source",
        "work_item_relations",
        ["user_id", "source_work_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_work_item_relations_user_target",
        "work_item_relations",
        ["user_id", "target_work_item_id"],
        unique=False,
    )

    op.create_table(
        "note_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=True),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "num_nonnulls(work_item_id, person_id, topic_id) = 1",
            name="ck_note_links_one_target",
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["notes.id"],
            name="fk_note_links_note_id_notes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["people.id"],
            name="fk_note_links_person_id_people",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name="fk_note_links_topic_id_topics",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_note_links_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_note_links_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_note_links"),
        sa.UniqueConstraint(
            "note_id",
            "person_id",
            name="uq_note_links_note_person",
        ),
        sa.UniqueConstraint(
            "note_id",
            "topic_id",
            name="uq_note_links_note_topic",
        ),
        sa.UniqueConstraint(
            "note_id",
            "work_item_id",
            name="uq_note_links_note_work_item",
        ),
    )
    op.create_index(
        "ix_note_links_person_id",
        "note_links",
        ["person_id"],
        unique=False,
    )
    op.create_index(
        "ix_note_links_topic_id",
        "note_links",
        ["topic_id"],
        unique=False,
    )
    op.create_index(
        "ix_note_links_user_note",
        "note_links",
        ["user_id", "note_id"],
        unique=False,
    )
    op.create_index(
        "ix_note_links_work_item_id",
        "note_links",
        ["work_item_id"],
        unique=False,
    )

    op.create_table(
        "work_item_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('created', 'updated', 'status_changed', 'linked')",
            name="ck_work_item_events_type",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_work_item_events_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name="fk_work_item_events_work_item_id_work_items",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_work_item_events"),
    )
    op.create_index(
        "ix_work_item_events_item_created_at",
        "work_item_events",
        ["work_item_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_work_item_events_user_id",
        "work_item_events",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_work_item_events_user_id", table_name="work_item_events")
    op.drop_index(
        "ix_work_item_events_item_created_at",
        table_name="work_item_events",
    )
    op.drop_table("work_item_events")

    op.drop_index("ix_note_links_work_item_id", table_name="note_links")
    op.drop_index("ix_note_links_user_note", table_name="note_links")
    op.drop_index("ix_note_links_topic_id", table_name="note_links")
    op.drop_index("ix_note_links_person_id", table_name="note_links")
    op.drop_table("note_links")

    op.drop_index(
        "ix_work_item_relations_user_target",
        table_name="work_item_relations",
    )
    op.drop_index(
        "ix_work_item_relations_user_source",
        table_name="work_item_relations",
    )
    op.drop_table("work_item_relations")

    op.drop_index("ix_work_item_people_user_id", table_name="work_item_people")
    op.drop_index("ix_work_item_people_person_id", table_name="work_item_people")
    op.drop_table("work_item_people")

    op.drop_index("ix_work_items_user_status", table_name="work_items")
    op.drop_index("ix_work_items_user_due_at", table_name="work_items")
    op.drop_index("ix_work_items_topic_id", table_name="work_items")
    op.drop_index("ix_work_items_source_note_id", table_name="work_items")
    op.drop_table("work_items")

    op.drop_index("ix_people_user_normalized_name", table_name="people")
    op.drop_index("ix_people_user_active", table_name="people")
    op.drop_table("people")

    op.drop_index("uq_topics_user_normalized_name", table_name="topics")
    op.drop_index("ix_topics_user_active", table_name="topics")
    op.drop_table("topics")

    op.drop_constraint(
        "ck_notes_source_update_consistency",
        "notes",
        type_="check",
    )
    op.drop_constraint("ck_notes_source", "notes", type_="check")
    op.alter_column("notes", "telegram_update_id", nullable=False)
    op.create_check_constraint(
        "ck_notes_source",
        "notes",
        "source IN ('text', 'voice')",
    )
    op.create_check_constraint(
        "ck_notes_telegram_update_id_positive",
        "notes",
        "telegram_update_id > 0",
    )
