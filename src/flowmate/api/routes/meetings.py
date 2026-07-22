from datetime import UTC, date, datetime, time
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.provider import MeetingReviewProvider
from flowmate.ai.schemas import DraftItemType
from flowmate.api.dependencies import get_session
from flowmate.auth.dependencies import PwaIdentity, require_csrf, require_pwa_session
from flowmate.core.config import Settings, get_settings
from flowmate.meetings.capture import (
    CaptureConflictError,
    edit_capture_item,
    get_owned_capture,
    list_captures,
    remove_capture,
    serialize_capture,
)
from flowmate.meetings.enums import MeetingType
from flowmate.meetings.review import (
    MeetingReviewError,
    add_review_agenda_item,
    answer_review_item,
    confirm_review,
    generate_review,
    get_review,
    serialize_review,
    set_agenda_outcome,
    set_review_item_action,
    sync_removed_review_capture,
    sync_review_capture_item,
)
from flowmate.meetings.service import (
    ActiveMeetingExistsError,
    InvalidMeetingTransitionError,
    StaleMeetingError,
    add_participant,
    cancel_meeting,
    create_meeting,
    default_meeting_title,
    end_meeting,
    get_active_meeting,
    get_meeting,
    link_topic,
    list_recent_meetings,
    serialize_meeting,
    start_meeting,
)
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)
from flowmate.reminders.sync import ReminderPolicy
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.task_engine.conversion import DraftConversionError, DraftConversionService
from flowmate.task_engine.enums import WorkItemPriority
from flowmate.task_engine.remaining import DraftItemEdit

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartMeetingRequest(StrictRequest):
    client_action_id: UUID
    type: MeetingType
    title: str | None = Field(default=None, max_length=500)
    participant_ids: list[UUID] = Field(default_factory=list, max_length=50)
    topic_ids: list[UUID] = Field(default_factory=list, max_length=50)
    primary_topic_id: UUID | None = None

    @model_validator(mode="after")
    def validate_links(self) -> "StartMeetingRequest":
        if len(set(self.participant_ids)) != len(self.participant_ids):
            raise ValueError("participant_ids must be unique")
        if len(set(self.topic_ids)) != len(self.topic_ids):
            raise ValueError("topic_ids must be unique")
        if (
            self.primary_topic_id is not None
            and self.primary_topic_id not in self.topic_ids
        ):
            raise ValueError("primary_topic_id must be linked")
        return self


class MeetingActionRequest(StrictRequest):
    action: Literal["start", "end", "cancel"]
    client_action_id: UUID
    expected_revision: int = Field(ge=0)


class CaptureItemEditRequest(StrictRequest):
    expected_revision: int = Field(ge=0)
    item_type: DraftItemType
    title: str = Field(min_length=1, max_length=10_000)
    description: str | None = Field(default=None, max_length=20_000)
    priority: WorkItemPriority = WorkItemPriority.NORMAL
    topic_id: UUID | None = None
    person_ids: list[UUID] = Field(default_factory=list, max_length=50)
    local_date: date | None = None
    local_time: time | None = None


class CaptureActionRequest(StrictRequest):
    action: Literal["remove"]
    client_action_id: UUID
    expected_revision: int = Field(ge=0)


class ReviewActionRequest(StrictRequest):
    action: Literal["retry", "confirm_ready", "complete_with_inbox"]
    client_action_id: UUID
    expected_revision: int = Field(ge=0)


class ReviewItemActionRequest(StrictRequest):
    action: Literal["exclude", "include", "planner_on", "planner_off", "inbox"]
    expected_revision: int = Field(ge=0)


class ReviewClarificationRequest(StrictRequest):
    answer: str = Field(min_length=1, max_length=10_000)
    expected_revision: int = Field(ge=0)


class AgendaOutcomeRequest(StrictRequest):
    outcome: Literal["discussed", "answered", "deferred", "unresolved"]
    result: str | None = Field(default=None, max_length=20_000)


class AddAgendaRequest(StrictRequest):
    title: str = Field(min_length=1, max_length=10_000)


def _page(
    items: list[dict[str, object]], limit: int, offset: int, has_more: bool
) -> dict[str, object]:
    return {"items": items, "limit": limit, "offset": offset, "has_more": has_more}


async def _timezone(
    session: AsyncSession, identity: PwaIdentity, settings: Settings
) -> ZoneInfo:
    preferences = await get_effective_notification_preferences(
        session,
        identity.user.id,
        NotificationDefaults.from_settings(settings),
    )
    return ZoneInfo(preferences.timezone)


def _review_provider(request: Request) -> MeetingReviewProvider | None:
    value: object | None = getattr(request.app.state, "meeting_review_provider", None)
    return value if isinstance(value, MeetingReviewProvider) else None


@router.get("/active")
async def active_meeting(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
) -> dict[str, object]:
    meeting = await get_active_meeting(session, identity.user.id)
    return {
        "meeting": await serialize_meeting(session, meeting)
        if meeting is not None
        else None
    }


@router.get("")
async def recent_meetings(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    page = await list_recent_meetings(
        session, identity.user.id, limit=limit, offset=offset
    )
    return _page(page.items, page.limit, page.offset, page.has_more)


@router.get("/{meeting_id}")
async def meeting_detail(
    meeting_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
) -> dict[str, object]:
    meeting = await get_meeting(session, identity.user.id, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    review = await get_review(session, identity.user.id, meeting_id)
    return {
        "meeting": await serialize_meeting(session, meeting),
        "review": await serialize_review(session, review)
        if review is not None
        else None,
    }


@router.get("/{meeting_id}/review")
async def meeting_review(
    meeting_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
) -> dict[str, object]:
    meeting = await get_meeting(session, identity.user.id, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    review = await get_review(session, identity.user.id, meeting_id)
    return {"review": await serialize_review(session, review) if review else None}


@router.get("/{meeting_id}/captures")
async def meeting_captures(
    meeting_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    try:
        page = await list_captures(
            session,
            identity.user.id,
            meeting_id,
            limit=limit,
            offset=offset,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Meeting not found") from error
    return _page(page.items, page.limit, page.offset, page.has_more)


@router.patch("/{meeting_id}/captures/{capture_id}/items/{item_id}")
async def update_meeting_capture_item(
    meeting_id: UUID,
    capture_id: UUID,
    item_id: UUID,
    payload: CaptureItemEditRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    timezone = await _timezone(session, identity, settings)
    if payload.local_time is not None and payload.local_date is None:
        raise HTTPException(status_code=422, detail="Date is required for local time")
    due_at = (
        resolve_local_datetime(
            payload.local_date,
            (payload.local_time or time(9, 0)).replace(tzinfo=None),
            timezone,
        ).astimezone(UTC)
        if payload.local_date is not None
        else None
    )
    try:
        capture = await edit_capture_item(
            session,
            identity.user.id,
            meeting_id,
            capture_id,
            item_id,
            DraftItemEdit(
                item_type=payload.item_type,
                title=payload.title,
                description=payload.description,
                priority=payload.priority,
                topic_id=payload.topic_id,
                person_ids=tuple(payload.person_ids),
                due_at=due_at,
            ),
            expected_revision=payload.expected_revision,
            high_threshold=settings.ai_high_confidence_threshold,
            clarification_threshold=settings.ai_clarification_confidence_threshold,
            draft_ttl_hours=settings.draft_ttl_hours,
            now=datetime.now(UTC),
        )
        await sync_review_capture_item(session, identity.user.id, meeting_id, item_id)
    except CaptureConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        if "not found" in str(error):
            raise HTTPException(status_code=404, detail="Capture not found") from error
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"capture": await serialize_capture(session, capture)}


@router.post("/{meeting_id}/captures/{capture_id}/actions")
async def meeting_capture_action(
    meeting_id: UUID,
    capture_id: UUID,
    payload: CaptureActionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    capture = await get_owned_capture(session, identity.user.id, meeting_id, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="Capture not found")
    try:
        removed = await remove_capture(
            session,
            identity.user.id,
            meeting_id,
            capture_id,
            expected_revision=payload.expected_revision,
        )
        await sync_removed_review_capture(
            session, identity.user.id, meeting_id, capture_id
        )
    except CaptureConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"capture": await serialize_capture(session, removed)}


@router.post("")
async def create_and_start(
    payload: StartMeetingRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    now = datetime.now(UTC)
    title = payload.title
    if title is None or not title.strip():
        title = default_meeting_title(
            payload.type, now, await _timezone(session, identity, settings)
        )
    try:
        meeting = await create_meeting(
            session,
            identity.user.id,
            payload.type,
            title,
            client_action_id=payload.client_action_id,
            now=now,
        )
        for person_id in payload.participant_ids:
            await add_participant(session, identity.user.id, meeting.id, person_id)
        for topic_id in payload.topic_ids:
            await link_topic(
                session,
                identity.user.id,
                meeting.id,
                topic_id,
                primary=topic_id == payload.primary_topic_id,
            )
        meeting = await start_meeting(
            session,
            identity.user.id,
            meeting.id,
            client_action_id=payload.client_action_id,
            now=now,
        )
        return {"meeting": await serialize_meeting(session, meeting)}
    except (ActiveMeetingExistsError, InvalidMeetingTransitionError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error
    except ValueError as error:
        if "not found" in str(error):
            raise HTTPException(
                status_code=404, detail="Meeting context not found"
            ) from error
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{meeting_id}/actions")
async def meeting_action(
    meeting_id: UUID,
    payload: MeetingActionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    actions = {"start": start_meeting, "end": end_meeting, "cancel": cancel_meeting}
    try:
        meeting = await actions[payload.action](
            session,
            identity.user.id,
            meeting_id,
            expected_revision=payload.expected_revision,
            client_action_id=payload.client_action_id,
        )
        if payload.action != "end":
            return {"meeting": await serialize_meeting(session, meeting)}
        # Persist the capture cutoff before invoking an external AI provider.
        await session.commit()
        try:
            await generate_review(
                session,
                identity.user.id,
                meeting_id,
                _review_provider(request),
                high_threshold=settings.ai_high_confidence_threshold,
                clarification_threshold=(
                    settings.ai_clarification_confidence_threshold
                ),
            )
            await session.commit()
        except MeetingReviewError as error:
            await session.commit()
            raise HTTPException(
                status_code=503,
                detail="Meeting ended, but review generation can be retried",
            ) from error
        refreshed = await get_meeting(session, identity.user.id, meeting_id)
        refreshed_review = await get_review(session, identity.user.id, meeting_id)
        if refreshed is None or refreshed_review is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        return {
            "meeting": await serialize_meeting(session, refreshed),
            "review": await serialize_review(session, refreshed_review),
        }
    except StaleMeetingError as error:
        raise HTTPException(status_code=409, detail="Meeting changed") from error
    except InvalidMeetingTransitionError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        if "not found" in str(error):
            raise HTTPException(status_code=404, detail="Meeting not found") from error
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/{meeting_id}/review/actions")
async def meeting_review_action(
    meeting_id: UUID,
    payload: ReviewActionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    try:
        if payload.action == "retry":
            review = await generate_review(
                session,
                identity.user.id,
                meeting_id,
                _review_provider(request),
                high_threshold=settings.ai_high_confidence_threshold,
                clarification_threshold=(
                    settings.ai_clarification_confidence_threshold
                ),
            )
            return {"review": await serialize_review(session, review)}
        confirmation = await confirm_review(
            session,
            identity.user.id,
            meeting_id,
            expected_revision=payload.expected_revision,
            client_action_id=payload.client_action_id,
            move_incomplete_to_inbox=payload.action == "complete_with_inbox",
            conversion_service=DraftConversionService(
                reminder_policy=ReminderPolicy(
                    deadline_lead_minutes=settings.deadline_reminder_lead_minutes
                )
            ),
        )
        current_review = await get_review(session, identity.user.id, meeting_id)
        if current_review is None:
            raise MeetingReviewError("review not found")
        return {
            "review": await serialize_review(session, current_review),
            "confirmation": {
                "work_item_ids": confirmation.converted_ids,
                "note_ids": confirmation.note_ids,
                "inbox_count": confirmation.inbox_count,
                "completed": confirmation.completed,
            },
        }
    except (MeetingReviewError, DraftConversionError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{meeting_id}/review/items/{item_id}/actions")
async def meeting_review_item_action(
    meeting_id: UUID,
    item_id: UUID,
    payload: ReviewItemActionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    try:
        review = await set_review_item_action(
            session,
            identity.user.id,
            meeting_id,
            item_id,
            action=payload.action,
            expected_revision=payload.expected_revision,
        )
    except MeetingReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"review": await serialize_review(session, review)}


@router.post("/{meeting_id}/review/items/{item_id}/clarification")
async def meeting_review_clarification(
    meeting_id: UUID,
    item_id: UUID,
    payload: ReviewClarificationRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    try:
        review = await answer_review_item(
            session,
            identity.user.id,
            meeting_id,
            item_id,
            payload.answer,
            expected_revision=payload.expected_revision,
        )
    except MeetingReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"review": await serialize_review(session, review)}


@router.post("/{meeting_id}/review/agenda/{entry_id}")
async def meeting_review_agenda_action(
    meeting_id: UUID,
    entry_id: UUID,
    payload: AgendaOutcomeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    try:
        await set_agenda_outcome(
            session,
            identity.user.id,
            meeting_id,
            entry_id,
            payload.outcome,
            payload.result,
        )
        review = await get_review(session, identity.user.id, meeting_id)
        if review is None:
            raise MeetingReviewError("review not found")
    except MeetingReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"review": await serialize_review(session, review)}


@router.post("/{meeting_id}/review/agenda")
async def add_meeting_review_agenda(
    meeting_id: UUID,
    payload: AddAgendaRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    try:
        await add_review_agenda_item(
            session, identity.user.id, meeting_id, payload.title
        )
        review = await get_review(session, identity.user.id, meeting_id)
        if review is None:
            raise MeetingReviewError("review not found")
    except MeetingReviewError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"review": await serialize_review(session, review)}
