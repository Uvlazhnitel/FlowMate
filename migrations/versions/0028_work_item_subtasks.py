"""Add one-level work item subtasks.

Revision ID: 0028_work_item_subtasks
Revises: 0027_work_item_workspace_move
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_work_item_subtasks"
down_revision: str | None = "0027_work_item_workspace_move"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RELATION_TYPES = (
    "related_to",
    "blocked_by",
    "after_completion",
    "created_from",
    "waiting_for",
    "subtask",
)


def _quoted(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(repr(value) for value in values) + ")"


def upgrade() -> None:
    op.drop_constraint(
        "ck_work_item_relations_type", "work_item_relations", type_="check"
    )
    op.create_check_constraint(
        "ck_work_item_relations_type",
        "work_item_relations",
        f"relation_type IN {_quoted(RELATION_TYPES)}",
    )
    op.create_index(
        "uq_work_item_relations_subtask_target",
        "work_item_relations",
        ["target_work_item_id"],
        unique=True,
        postgresql_where=sa.text("relation_type = 'subtask'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_work_item_relations_subtask_target", table_name="work_item_relations"
    )
    op.execute("DELETE FROM work_item_relations WHERE relation_type = 'subtask'")
    op.drop_constraint(
        "ck_work_item_relations_type", "work_item_relations", type_="check"
    )
    op.create_check_constraint(
        "ck_work_item_relations_type",
        "work_item_relations",
        f"relation_type IN {_quoted(RELATION_TYPES[:-1])}",
    )
