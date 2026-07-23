from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal, cast
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.api.dependencies import get_session
from flowmate.auth.dependencies import PwaIdentity, require_csrf, require_pwa_session
from flowmate.core.config import Settings, get_settings
from flowmate.reminders.actions import snooze_work_item_reminder
from flowmate.reminders.preferences import (
    EffectiveNotificationPreferences,
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.enums import PlannerStatus, WorkItemPriority, WorkItemType
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    StaleWorkItemError,
    add_decision_from_work_item,
    add_work_item_note,
    archive_work_item,
    bind_client_action,
    cancel_work_item,
    change_planner_status,
    complete_work_item,
    convert_work_item_to_task,
    edit_work_item,
    mark_waiting_received,
    reopen_work_item,
    reschedule_work_item,
)
from flowmate.task_engine.operational import (
    build_work_item_cards,
    dashboard_snapshot,
    get_owned_person,
    get_owned_topic,
    list_agenda,
    list_context_content,
    list_people_summary,
    list_today_section,
    list_topics_summary,
)
from flowmate.task_engine.queries import PersonScope

router = APIRouter(prefix="/api/v1", tags=["pwa-operations"])

TodaySection = Literal["overdue", "due_today", "follow_ups", "waiting", "questions"]
TopicSection = Literal["active", "people", "notes", "decisions", "history"]
PersonSection = Literal[
    "follow_ups", "waiting", "questions", "topics", "notes", "history"
]


class WorkItemActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_action_id: UUID
    expected_revision: int = Field(ge=0)


class SimpleWorkItemAction(WorkItemActionBase):
    action: Literal[
        "complete",
        "reopen",
        "cancel",
        "waiting_received",
        "agenda_discussed",
        "question_answered",
        "convert_to_task",
        "archive",
        "planner_transferred",
        "planner_not_required",
        "planner_update_required",
        "planner_needs_transfer",
    ]


class ContentWorkItemAction(WorkItemActionBase):
    action: Literal["add_note", "add_result", "add_decision"]
    content: str = Field(min_length=1, max_length=10_000)


class DateWorkItemAction(WorkItemActionBase):
    action: Literal["reschedule", "defer"]
    local_date: date
    local_time: time


class SnoozeWorkItemAction(WorkItemActionBase):
    action: Literal["snooze"]
    duration_minutes: int | None = Field(default=None, ge=1, le=10_080)
    local_date: date | None = None
    local_time: time | None = None
    reminder_id: UUID
    reminder_revision: int = Field(ge=0)


class EditWorkItemAction(WorkItemActionBase):
    action: Literal["edit"]
    title: str = Field(min_length=1, max_length=10_000)
    description: str | None = Field(default=None, max_length=20_000)
    item_type: WorkItemType
    priority: WorkItemPriority
    topic_id: UUID | None = None
    person_ids: list[UUID] = Field(default_factory=list, max_length=50)
    date_changed: bool = False
    local_date: date | None = None
    local_time: time | None = None


WorkItemActionRequest = Annotated[
    SimpleWorkItemAction
    | ContentWorkItemAction
    | DateWorkItemAction
    | SnoozeWorkItemAction
    | EditWorkItemAction,
    Field(discriminator="action"),
]


async def _preferences(
    session: AsyncSession, identity: PwaIdentity, settings: Settings
) -> EffectiveNotificationPreferences:
    return await get_effective_notification_preferences(
        session,
        identity.user.id,
        NotificationDefaults.from_settings(settings),
    )


def _clock() -> datetime:
    return datetime.now(UTC)


def _page_payload(page: object, *, timezone: str | None = None) -> dict[str, object]:
    payload = {
        "items": page.items,  # type: ignore[attr-defined]
        "limit": page.limit,  # type: ignore[attr-defined]
        "offset": page.offset,  # type: ignore[attr-defined]
        "has_more": page.has_more,  # type: ignore[attr-defined]
    }
    if timezone is not None:
        payload["timezone"] = timezone
    return payload


@router.get("/dashboard")
async def dashboard(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    preferences = await _preferences(session, identity, settings)
    payload = await dashboard_snapshot(
        session, identity.user.id, now=_clock(), preferences=preferences
    )
    return {"timezone": preferences.timezone, **payload}


@router.get("/today")
async def today(
    section: TodaySection,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    preferences = await _preferences(session, identity, settings)
    page = await list_today_section(
        session,
        identity.user.id,
        section,
        now=_clock(),
        preferences=preferences,
        limit=limit,
        offset=offset,
    )
    return {"section": section, **_page_payload(page, timezone=preferences.timezone)}


@router.get("/topics")
async def topics(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    q: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    return _page_payload(
        await list_topics_summary(
            session,
            identity.user.id,
            query=q,
            now=_clock(),
            limit=limit,
            offset=offset,
        )
    )


@router.get("/topics/{topic_id}")
async def topic_details(
    topic_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
) -> dict[str, object]:
    topic = await get_owned_topic(session, identity.user.id, topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    return {
        "id": topic.id,
        "name": topic.name,
        "description": topic.description,
        "created_at": topic.created_at,
        "updated_at": topic.updated_at,
    }


@router.get("/topics/{topic_id}/content")
async def topic_content(
    topic_id: UUID,
    section: TopicSection,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    if await get_owned_topic(session, identity.user.id, topic_id) is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    page = await list_context_content(
        session,
        identity.user.id,
        owner_type="topic",
        owner_id=topic_id,
        section=section,
        now=_clock(),
        limit=limit,
        offset=offset,
    )
    return {"section": section, **_page_payload(page)}


@router.get("/people")
async def people(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    q: str | None = None,
    scope: PersonScope = "work",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    return _page_payload(
        await list_people_summary(
            session,
            identity.user.id,
            scope=scope,
            query=q,
            now=_clock(),
            limit=limit,
            offset=offset,
        )
    )


@router.get("/people/{person_id}")
async def person_details(
    person_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
) -> dict[str, object]:
    person = await get_owned_person(session, identity.user.id, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return {
        "id": person.id,
        "display_name": person.display_name,
        "role": person.role,
        "notes": person.notes,
        "created_at": person.created_at,
        "updated_at": person.updated_at,
    }


@router.get("/people/{person_id}/content")
async def person_content(
    person_id: UUID,
    section: PersonSection,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    if await get_owned_person(session, identity.user.id, person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")
    page = await list_context_content(
        session,
        identity.user.id,
        owner_type="person",
        owner_id=person_id,
        section=section,
        now=_clock(),
        limit=limit,
        offset=offset,
    )
    return {"section": section, **_page_payload(page)}


@router.get("/agenda")
async def agenda(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    group_kind: Literal["person", "topic", "unassigned"] | None = None,
    group_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    preferences = await _preferences(session, identity, settings)
    page = await list_agenda(
        session,
        identity.user.id,
        group_kind=group_kind,
        group_id=group_id,
        now=_clock(),
        limit=limit,
        offset=offset,
    )
    return _page_payload(page, timezone=preferences.timezone)


def _target_datetime(
    payload: DateWorkItemAction | SnoozeWorkItemAction, timezone: str
) -> datetime:
    if payload.local_date is None or payload.local_time is None:
        raise HTTPException(status_code=422, detail="Local date and time are required")
    return resolve_local_datetime(
        payload.local_date, payload.local_time.replace(tzinfo=None), ZoneInfo(timezone)
    ).astimezone(UTC)


@router.post("/work-items/{work_item_id}/actions")
async def work_item_action(
    work_item_id: UUID,
    payload: WorkItemActionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    bind_client_action(session, payload.client_action_id)
    user_id = identity.user.id
    try:
        if payload.action in {"complete", "agenda_discussed", "question_answered"}:
            result = await complete_work_item(
                session,
                user_id,
                work_item_id,
                None,
                expected_revision=payload.expected_revision,
            )
        elif payload.action == "reopen":
            result = await reopen_work_item(
                session,
                user_id,
                work_item_id,
                None,
                expected_revision=payload.expected_revision,
            )
        elif payload.action == "cancel":
            result = await cancel_work_item(
                session,
                user_id,
                work_item_id,
                None,
                expected_revision=payload.expected_revision,
            )
        elif payload.action in {"reschedule", "defer"}:
            date_payload = cast(DateWorkItemAction, payload)
            preferences = await _preferences(session, identity, settings)
            result = await reschedule_work_item(
                session,
                user_id,
                work_item_id,
                None,
                _target_datetime(date_payload, preferences.timezone),
                expected_revision=payload.expected_revision,
            )
        elif payload.action in {"add_note", "add_result"}:
            content_payload = cast(ContentWorkItemAction, payload)
            result, _ = await add_work_item_note(
                session,
                user_id,
                work_item_id,
                None,
                content_payload.content,
                expected_revision=payload.expected_revision,
            )
        elif payload.action == "waiting_received":
            result = await mark_waiting_received(
                session,
                user_id,
                work_item_id,
                None,
                expected_revision=payload.expected_revision,
            )
        elif payload.action == "convert_to_task":
            result = await convert_work_item_to_task(
                session,
                user_id,
                work_item_id,
                None,
                expected_revision=payload.expected_revision,
            )
        elif payload.action == "archive":
            result = await archive_work_item(
                session,
                user_id,
                work_item_id,
                None,
                expected_revision=payload.expected_revision,
            )
        elif payload.action.startswith("planner_"):
            planner_targets = {
                "planner_transferred": PlannerStatus.TRANSFERRED,
                "planner_not_required": PlannerStatus.NOT_REQUIRED,
                "planner_update_required": PlannerStatus.UPDATE_REQUIRED,
                "planner_needs_transfer": PlannerStatus.NEEDS_TRANSFER,
            }
            result = await change_planner_status(
                session,
                user_id,
                work_item_id,
                planner_targets[payload.action],
                expected_revision=payload.expected_revision,
            )
        elif payload.action == "edit":
            scheduled_at = None
            if payload.date_changed:
                if (payload.local_date is None) != (payload.local_time is None):
                    raise HTTPException(
                        status_code=422,
                        detail="Local date and time must be provided together",
                    )
                if payload.local_date is not None and payload.local_time is not None:
                    preferences = await _preferences(session, identity, settings)
                    scheduled_at = resolve_local_datetime(
                        payload.local_date,
                        payload.local_time.replace(tzinfo=None),
                        ZoneInfo(preferences.timezone),
                    ).astimezone(UTC)
            result = await edit_work_item(
                session,
                user_id,
                work_item_id,
                title=payload.title,
                description=payload.description,
                item_type=payload.item_type,
                priority=payload.priority,
                topic_id=payload.topic_id,
                person_ids=tuple(payload.person_ids),
                update_schedule=payload.date_changed,
                scheduled_at=scheduled_at,
                expected_revision=payload.expected_revision,
            )
        elif payload.action == "add_decision":
            result, decision = await add_decision_from_work_item(
                session,
                user_id,
                work_item_id,
                None,
                payload.content,
                expected_revision=payload.expected_revision,
            )
            await session.refresh(result.work_item)
            cards = await build_work_item_cards(
                session, user_id, [result.work_item], now=_clock()
            )
            return {
                "changed": result.changed,
                "work_item": cards[0],
                "decision_id": decision.id,
            }
        else:
            snooze_payload = cast(SnoozeWorkItemAction, payload)
            preferences = await _preferences(session, identity, settings)
            if snooze_payload.duration_minutes is not None:
                reminder, changed = await snooze_work_item_reminder(
                    session,
                    user_id,
                    snooze_payload.reminder_id,
                    None,
                    duration=timedelta(minutes=snooze_payload.duration_minutes),
                    expected_revision=snooze_payload.reminder_revision,
                )
            else:
                reminder, changed = await snooze_work_item_reminder(
                    session,
                    user_id,
                    snooze_payload.reminder_id,
                    None,
                    until=_target_datetime(snooze_payload, preferences.timezone),
                    expected_revision=snooze_payload.reminder_revision,
                )
            return {"changed": changed, "reminder_id": reminder.id}
    except StaleWorkItemError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Work item changed"
        ) from error
    except InvalidWorkItemTransitionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error
    except ValueError as error:
        message = str(error)
        if "not found" in message:
            raise HTTPException(
                status_code=404, detail="Work item not found"
            ) from error
        raise HTTPException(status_code=422, detail=message) from error
    await session.refresh(result.work_item)
    cards = await build_work_item_cards(
        session, user_id, [result.work_item], now=_clock()
    )
    return {"changed": result.changed, "work_item": cards[0]}
