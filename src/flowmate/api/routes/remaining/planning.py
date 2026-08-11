from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.api.dependencies import get_session
from flowmate.auth.dependencies import PwaIdentity, require_pwa_session
from flowmate.core.config import Settings, get_settings
from flowmate.reminders.preferences import (
    EffectiveNotificationPreferences,
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.enums import PlannerStatus, WorkItemType
from flowmate.task_engine.operational import PageResult
from flowmate.task_engine.remaining import (
    list_planner_queue,
    list_timeline,
)

router = APIRouter()


def _page_payload(page: PageResult) -> dict[str, object]:
    return {
        "items": page.items,
        "limit": page.limit,
        "offset": page.offset,
        "has_more": page.has_more,
    }


def _now() -> datetime:
    return datetime.now(UTC)


async def _preferences(
    session: AsyncSession, identity: PwaIdentity, settings: Settings
) -> EffectiveNotificationPreferences:
    return await get_effective_notification_preferences(
        session,
        identity.user.id,
        NotificationDefaults.from_settings(settings),
    )


@router.get("/planner-queue")
async def planner_queue(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    try:
        statuses = (
            tuple(PlannerStatus(value) for value in status_filter.split(","))
            if status_filter
            else (PlannerStatus.NEEDS_TRANSFER, PlannerStatus.UPDATE_REQUIRED)
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid Planner status") from error
    return _page_payload(
        await list_planner_queue(
            session,
            identity.user.id,
            statuses=statuses,
            query=q,
            now=_now(),
            limit=limit,
            offset=offset,
        )
    )


@router.get("/timeline")
async def timeline(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    topic_id: UUID | None = None,
    person_id: UUID | None = None,
    event_type: str | None = None,
    work_item_type: WorkItemType | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    preferences = await _preferences(session, identity, settings)
    zone = ZoneInfo(preferences.timezone)
    start = (
        resolve_local_datetime(date_from, time.min, zone).astimezone(UTC)
        if date_from
        else None
    )
    end = (
        resolve_local_datetime(date_to + timedelta(days=1), time.min, zone).astimezone(
            UTC
        )
        if date_to
        else None
    )
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail="Date range is invalid")
    return {
        "timezone": preferences.timezone,
        **_page_payload(
            await list_timeline(
                session,
                identity.user.id,
                start=start,
                end=end,
                topic_id=topic_id,
                person_id=person_id,
                event_type=event_type,
                work_item_type=work_item_type,
                limit=limit,
                offset=offset,
            )
        ),
    }
