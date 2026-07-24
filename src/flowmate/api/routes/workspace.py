from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.api.dependencies import get_session
from flowmate.api.schemas import PwaUserResponse
from flowmate.auth.dependencies import PwaIdentity, require_csrf
from flowmate.core.config import Settings, get_settings
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.stabilization.audit import record_audit_event
from flowmate.workspace_service import WorkspaceSwitchBlockedError, switch_workspace

router = APIRouter(prefix="/api/v1/workspace", tags=["pwa-workspace"])


class WorkspaceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workspace: Literal["personal", "work"]


@router.put(
    "",
    response_model=PwaUserResponse,
    responses={401: {}, 403: {}, 409: {}},
)
async def update_workspace(
    payload: WorkspaceUpdateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PwaUserResponse:
    try:
        user = await switch_workspace(
            session,
            identity.user.id,
            payload.workspace,
        )
    except WorkspaceSwitchBlockedError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error.blocker.message,
        ) from error
    preferences = await get_effective_notification_preferences(
        session,
        user.id,
        NotificationDefaults.from_settings(settings),
    )
    await record_audit_event(
        session,
        actor_kind="pwa",
        action="workspace.switched",
        outcome="success",
        user_id=user.id,
        entity_kind="workspace",
        safe_metadata={"workspace": user.active_workspace},
    )
    return PwaUserResponse(
        id=user.id,
        display_name=user.display_name,
        timezone=preferences.timezone,
        default_snooze_minutes=preferences.default_snooze_minutes,
        date_display_format=preferences.date_display_format,
        time_display_format=preferences.time_display_format,
        active_workspace=user.active_workspace,
    )
