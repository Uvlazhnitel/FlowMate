from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import String, event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, Session, mapped_column, with_loader_criteria


class Workspace(StrEnum):
    PERSONAL = "personal"
    WORK = "work"


WORKSPACE_VALUES = tuple(value.value for value in Workspace)
WORKSPACE_LABELS = {
    Workspace.PERSONAL.value: "Личное",
    Workspace.WORK.value: "Работа",
}
SESSION_WORKSPACE_KEY = "flowmate_workspace"
SESSION_USER_ID_KEY = "flowmate_workspace_user_id"
WorkspaceReadScope = Literal["all", "work", "personal"]
WorkspaceCounts = dict[str, int]


class WorkspaceScoped:
    workspace: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=Workspace.PERSONAL.value,
        server_default=Workspace.PERSONAL.value,
    )


def normalize_workspace(value: Workspace | str) -> str:
    return Workspace(value).value


def activate_workspace(
    session: AsyncSession,
    *,
    user_id: UUID,
    workspace: Workspace | str,
) -> str:
    normalized = normalize_workspace(workspace)
    session.info[SESSION_USER_ID_KEY] = user_id
    session.info[SESSION_WORKSPACE_KEY] = normalized
    return normalized


def active_workspace(session: AsyncSession) -> str | None:
    value = session.info.get(SESSION_WORKSPACE_KEY)
    return value if isinstance(value, str) else None


def normalize_workspace_read_scope(value: str | None) -> WorkspaceReadScope:
    if value == "work":
        return "work"
    if value == "personal":
        return "personal"
    return "all"


def workspace_counts(*, work: int = 0, personal: int = 0) -> WorkspaceCounts:
    return {"all": work + personal, "work": work, "personal": personal}


@contextmanager
def workspace_context(
    session: AsyncSession,
    *,
    user_id: UUID,
    workspace: Workspace | str,
) -> Iterator[None]:
    previous_workspace = session.info.get(SESSION_WORKSPACE_KEY)
    previous_user_id = session.info.get(SESSION_USER_ID_KEY)
    activate_workspace(session, user_id=user_id, workspace=workspace)
    try:
        yield
    finally:
        if previous_workspace is None:
            session.info.pop(SESSION_WORKSPACE_KEY, None)
        else:
            session.info[SESSION_WORKSPACE_KEY] = previous_workspace
        if previous_user_id is None:
            session.info.pop(SESSION_USER_ID_KEY, None)
        else:
            session.info[SESSION_USER_ID_KEY] = previous_user_id


async def owned_entity_workspace(
    session: AsyncSession,
    model: Any,
    *,
    user_id: UUID,
    entity_id: UUID,
) -> str | None:
    return await session.scalar(
        select(model.workspace)
        .where(model.id == entity_id, model.user_id == user_id)
        .execution_options(include_all_workspaces=True)
    )


@event.listens_for(Session, "do_orm_execute")
def _apply_workspace_filter(execute_state: Any) -> None:
    workspace = execute_state.session.info.get(SESSION_WORKSPACE_KEY)
    if (
        workspace is None
        or execute_state.execution_options.get("include_all_workspaces")
        or not execute_state.is_select
    ):
        return
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            WorkspaceScoped,
            lambda model: model.workspace == workspace,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _stamp_new_workspace(
    session: Session,
    _flush_context: Any,
    _instances: Any,
) -> None:
    workspace = session.info.get(SESSION_WORKSPACE_KEY)
    if workspace is None:
        return
    for value in session.new:
        if isinstance(value, WorkspaceScoped):
            value.workspace = workspace
