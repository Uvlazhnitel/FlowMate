from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import (
    DraftSession,
    User,
    WorkItemActionSession,
)
from flowmate.workspaces import Workspace, activate_workspace, normalize_workspace


@dataclass(frozen=True, slots=True)
class WorkspaceSwitchBlocker:
    code: str
    message: str


class WorkspaceSwitchBlockedError(ValueError):
    def __init__(self, blocker: WorkspaceSwitchBlocker) -> None:
        super().__init__(blocker.message)
        self.blocker = blocker


async def workspace_switch_blocker(
    session: AsyncSession,
    user_id: UUID,
) -> WorkspaceSwitchBlocker | None:
    checks = (
        (
            "active_draft",
            "Сначала подтвердите или отмените текущий черновик.",
            select(
                exists().where(
                    DraftSession.user_id == user_id,
                    DraftSession.status.in_(
                        ("parsing", "needs_clarification", "ready")
                    ),
                )
            ),
        ),
        (
            "active_action",
            "Сначала завершите или отмените текущее действие.",
            select(
                exists().where(
                    WorkItemActionSession.user_id == user_id,
                    WorkItemActionSession.status == "open",
                )
            ),
        ),
    )
    for code, message, statement in checks:
        if await session.scalar(statement):
            return WorkspaceSwitchBlocker(code=code, message=message)
    return None


async def switch_workspace(
    session: AsyncSession,
    user_id: UUID,
    workspace: Workspace | str,
) -> User:
    target = normalize_workspace(workspace)
    user = await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )
    if user is None or not user.is_active:
        raise ValueError("User not found")
    if user.active_workspace == target:
        activate_workspace(session, user_id=user.id, workspace=target)
        return user
    blocker = await workspace_switch_blocker(session, user.id)
    if blocker is not None:
        raise WorkspaceSwitchBlockedError(blocker)
    user.active_workspace = target
    await session.flush()
    activate_workspace(session, user_id=user.id, workspace=target)
    return user
