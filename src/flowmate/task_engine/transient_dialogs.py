from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.drafts import transition_draft
from flowmate.db.models import DraftSession, WorkItemActionSession
from flowmate.db.models.draft import OPEN_DRAFT_STATUSES
from flowmate.task_engine.action_sessions import finish_action_session


@dataclass(frozen=True, slots=True)
class CancelledTransientDialogs:
    action_sessions: int = 0
    drafts: int = 0

    @property
    def total(self) -> int:
        return self.action_sessions + self.drafts


async def cancel_transient_dialogs(
    session: AsyncSession,
    user_id: UUID,
) -> CancelledTransientDialogs:
    actions = list(
        await session.scalars(
            select(WorkItemActionSession)
            .where(
                WorkItemActionSession.user_id == user_id,
                WorkItemActionSession.status == "open",
            )
            .with_for_update()
        )
    )
    for action in actions:
        await finish_action_session(session, action, status="cancelled")

    drafts = list(
        await session.scalars(
            select(DraftSession)
            .where(
                DraftSession.user_id == user_id,
                DraftSession.status.in_(OPEN_DRAFT_STATUSES),
            )
            .with_for_update()
        )
    )
    for draft in drafts:
        await transition_draft(session, draft, "cancelled")
    return CancelledTransientDialogs(len(actions), len(drafts))
