from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.ai.schemas import DraftItemType, DraftReadiness
from flowmate.api.dependencies import get_session
from flowmate.auth.dependencies import PwaIdentity, require_csrf, require_pwa_session
from flowmate.core.config import Settings, get_settings
from flowmate.db.drafts import transition_draft
from flowmate.db.models import Note, Person, Topic
from flowmate.reminders.digests import cancel_future_digests
from flowmate.reminders.enums import ReminderType
from flowmate.reminders.preferences import (
    EffectiveNotificationPreferences,
    NotificationDefaults,
    get_effective_notification_preferences,
    get_or_create_notification_preferences,
    validate_clock_time,
    validate_timezone,
)
from flowmate.reminders.timezone import resolve_local_datetime
from flowmate.stabilization.audit import record_audit_event
from flowmate.task_engine.conversion import (
    DraftConversionError,
    DraftConversionService,
)
from flowmate.task_engine.enums import PlannerStatus, WorkItemPriority, WorkItemType
from flowmate.task_engine.management import (
    InvalidWorkItemTransitionError,
    archive_work_item,
    bind_client_action,
)
from flowmate.task_engine.operational import PageResult
from flowmate.task_engine.remaining import (
    DraftItemEdit,
    edit_draft_item,
    get_owned_draft,
    list_inbox,
    list_planner_queue,
    list_timeline,
    serialize_draft,
)
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    normalize_aliases,
    normalize_optional_text,
    normalize_required_text,
)

router = APIRouter(prefix="/api/v1", tags=["pwa-remaining"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftItemEditRequest(StrictRequest):
    expected_revision: int = Field(ge=0)
    item_type: DraftItemType
    title: str = Field(min_length=1, max_length=10_000)
    description: str | None = Field(default=None, max_length=20_000)
    priority: WorkItemPriority = WorkItemPriority.NORMAL
    topic_id: UUID | None = None
    person_ids: list[UUID] = Field(default_factory=list, max_length=50)
    local_date: date | None = None
    local_time: time | None = None


class DraftActionRequest(StrictRequest):
    action: Literal["confirm", "save_as_note", "cancel", "recover"]
    expected_revision: int = Field(ge=0)
    accept_uncertainty: bool = False


class NoteActionRequest(StrictRequest):
    action: Literal["keep", "archive"]


class BulkEntry(StrictRequest):
    kind: Literal["draft", "note", "work_item", "meeting_review"]
    id: UUID
    expected_revision: int | None = Field(default=None, ge=0)
    client_action_id: UUID | None = None


class BulkActionRequest(StrictRequest):
    action: Literal["cancel", "archive", "keep"]
    entries: list[BulkEntry] = Field(min_length=1, max_length=50)


class PreferencesRequest(StrictRequest):
    timezone: str = Field(min_length=1, max_length=64)
    morning_digest_enabled: bool
    morning_digest_time: time
    evening_digest_enabled: bool
    evening_digest_time: time
    quiet_hours_enabled: bool
    quiet_hours_start: time
    quiet_hours_end: time
    default_snooze_minutes: int = Field(ge=1, le=10_080)
    send_empty_digests: bool
    date_display_format: Literal["day_month_year", "year_month_day"]
    time_display_format: Literal["24h", "12h"]


class TopicRequest(StrictRequest):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    is_active: bool = True


class PersonRequest(StrictRequest):
    display_name: str = Field(min_length=1, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=10_000)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    is_active: bool = True


def _page_payload(page: PageResult) -> dict[str, object]:
    return {
        "items": page.items,
        "limit": page.limit,
        "offset": page.offset,
        "has_more": page.has_more,
    }


def _topic_payload(topic: Topic) -> dict[str, object]:
    return {
        "id": topic.id,
        "name": topic.name,
        "description": topic.description,
        "aliases": topic.aliases,
        "is_active": topic.is_active,
    }


def _person_payload(person: Person) -> dict[str, object]:
    return {
        "id": person.id,
        "display_name": person.display_name,
        "role": person.role,
        "notes": person.notes,
        "aliases": person.aliases,
        "is_active": person.is_active,
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


@router.get("/inbox")
async def inbox(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    kind: Literal["draft", "work_item", "note", "meeting_review"] | None = None,
    reason: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    return _page_payload(
        await list_inbox(
            session,
            identity.user.id,
            now=_now(),
            low_confidence_threshold=settings.ai_high_confidence_threshold,
            kind=kind,
            reason=reason,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/inbox/drafts/{draft_id}")
async def inbox_draft(
    draft_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
) -> dict[str, object]:
    draft = await get_owned_draft(session, identity.user.id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    note = await session.scalar(
        select(Note).where(
            Note.id == draft.source_note_id, Note.user_id == identity.user.id
        )
    )
    return await serialize_draft(
        session,
        draft,
        source_content=(note.content or "") if note is not None else "",
    )


@router.patch("/inbox/drafts/{draft_id}/items/{item_id}")
async def update_draft_item(
    draft_id: UUID,
    item_id: UUID,
    payload: DraftItemEditRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    preferences = await _preferences(session, identity, settings)
    if payload.local_time is not None and payload.local_date is None:
        raise HTTPException(status_code=422, detail="Date is required for local time")
    due_at = (
        resolve_local_datetime(
            payload.local_date,
            (payload.local_time or time(9, 0)).replace(tzinfo=None),
            ZoneInfo(preferences.timezone),
        ).astimezone(UTC)
        if payload.local_date is not None
        else None
    )
    try:
        draft = await edit_draft_item(
            session,
            identity.user.id,
            draft_id,
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
            ttl_hours=settings.draft_ttl_hours,
            now=_now(),
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    note = await session.get(Note, draft.source_note_id)
    return await serialize_draft(
        session,
        draft,
        source_content=(note.content or "") if note is not None else "",
    )


@router.post("/inbox/drafts/{draft_id}/actions")
async def draft_action(
    draft_id: UUID,
    payload: DraftActionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    draft = await get_owned_draft(session, identity.user.id, draft_id, for_update=True)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    revision = int(draft.updated_at.astimezone(UTC).timestamp() * 1_000_000)
    if revision != payload.expected_revision:
        raise HTTPException(status_code=409, detail="Draft is stale")
    note = await session.scalar(
        select(Note)
        .where(Note.id == draft.source_note_id, Note.user_id == identity.user.id)
        .with_for_update()
    )
    if note is None:
        raise HTTPException(status_code=409, detail="Source note is unavailable")
    if payload.action == "confirm":
        uncertain = any(
            item.readiness != DraftReadiness.READY.value
            or item.confidence < settings.ai_high_confidence_threshold
            for item in draft.items
        )
        if uncertain and not payload.accept_uncertainty:
            raise HTTPException(
                status_code=409,
                detail="Explicit uncertainty confirmation is required",
            )
        try:
            result = await DraftConversionService().convert(
                session,
                draft_id=draft.id,
                user_id=identity.user.id,
                allow_incomplete=payload.accept_uncertainty,
            )
        except DraftConversionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        note.inbox_disposition = "kept"
        return {
            "status": "confirmed",
            "work_item_ids": [item.id for item in result.work_items],
            "note_ids": [value.id for value in result.notes],
        }
    if payload.action == "recover":
        if not draft.items or draft.analysis_payload is None:
            raise HTTPException(status_code=409, detail="Draft cannot be recovered")
        if all(item.readiness == DraftReadiness.READY.value for item in draft.items):
            target = "ready"
            await transition_draft(session, draft, "ready")
        else:
            target = "needs_clarification"
            await transition_draft(session, draft, "needs_clarification")
        draft.expires_at = _now() + timedelta(hours=settings.draft_ttl_hours)
        return {"status": target}
    note.inbox_disposition = "kept" if payload.action == "save_as_note" else "archived"
    await transition_draft(session, draft, "cancelled")
    return {"status": "cancelled", "note_disposition": note.inbox_disposition}


@router.post("/inbox/notes/{note_id}/actions")
async def note_action(
    note_id: UUID,
    payload: NoteActionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    note = await session.scalar(
        select(Note)
        .where(Note.id == note_id, Note.user_id == identity.user.id)
        .with_for_update()
    )
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    note.inbox_disposition = "kept" if payload.action == "keep" else "archived"
    await session.flush()
    return {"id": note.id, "disposition": note.inbox_disposition}


@router.post("/inbox/bulk-actions")
async def bulk_inbox_action(
    payload: BulkActionRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    allowed = {
        "cancel": {"draft"},
        "archive": {"note", "work_item"},
        "keep": {"note"},
    }
    if any(entry.kind not in allowed[payload.action] for entry in payload.entries):
        raise HTTPException(status_code=422, detail="Action is not safe for selection")
    for entry in payload.entries:
        if entry.kind == "draft":
            draft = await get_owned_draft(
                session, identity.user.id, entry.id, for_update=True
            )
            if draft is None:
                raise HTTPException(status_code=404, detail="Inbox item not found")
            revision = int(draft.updated_at.astimezone(UTC).timestamp() * 1_000_000)
            if entry.expected_revision is None or revision != entry.expected_revision:
                raise HTTPException(status_code=409, detail="Draft is stale")
            await transition_draft(session, draft, "cancelled")
            note = await session.get(Note, draft.source_note_id)
            if note is not None and note.user_id == identity.user.id:
                note.inbox_disposition = "archived"
        elif entry.kind == "note":
            note = await session.scalar(
                select(Note)
                .where(Note.id == entry.id, Note.user_id == identity.user.id)
                .with_for_update()
            )
            if note is None:
                raise HTTPException(status_code=404, detail="Inbox item not found")
            note.inbox_disposition = "kept" if payload.action == "keep" else "archived"
        else:
            if entry.expected_revision is None or entry.client_action_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="Work item revision and action ID are required",
                )
            bind_client_action(session, entry.client_action_id)
            try:
                await archive_work_item(
                    session,
                    identity.user.id,
                    entry.id,
                    None,
                    expected_revision=entry.expected_revision,
                )
            except (ValueError, InvalidWorkItemTransitionError) as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
    await session.flush()
    return {"processed": len(payload.entries)}


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


def _settings_payload(preferences: object, settings: Settings) -> dict[str, object]:
    return {
        "preferences": preferences,
        "providers": {
            "ai_configured": bool(
                settings.ai_provider and settings.openai_api_key and settings.ai_model
            ),
            "speech_configured": bool(
                settings.speech_provider
                and settings.openai_api_key
                and settings.speech_model
            ),
        },
    }


@router.get("/settings")
async def get_user_settings(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    return _settings_payload(await _preferences(session, identity, settings), settings)


@router.put("/settings/preferences")
async def update_user_settings(
    payload: PreferencesRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, object]:
    try:
        timezone = validate_timezone(payload.timezone)
        morning = validate_clock_time(payload.morning_digest_time)
        evening = validate_clock_time(payload.evening_digest_time)
        quiet_start = validate_clock_time(payload.quiet_hours_start)
        quiet_end = validate_clock_time(payload.quiet_hours_end)
        if quiet_start == quiet_end:
            raise ValueError("quiet hours start and end must differ")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    value = await get_or_create_notification_preferences(
        session,
        identity.user.id,
        NotificationDefaults.from_settings(settings),
    )
    value.timezone = timezone
    value.morning_digest_enabled = payload.morning_digest_enabled
    value.morning_digest_time = morning
    value.evening_digest_enabled = payload.evening_digest_enabled
    value.evening_digest_time = evening
    value.quiet_hours_enabled = payload.quiet_hours_enabled
    value.quiet_hours_start = quiet_start
    value.quiet_hours_end = quiet_end
    value.default_snooze_minutes = payload.default_snooze_minutes
    value.send_empty_digests = payload.send_empty_digests
    value.date_display_format = payload.date_display_format
    value.time_display_format = payload.time_display_format
    now = _now()
    await cancel_future_digests(
        session, identity.user.id, ReminderType.MORNING_DIGEST, now=now
    )
    await cancel_future_digests(
        session, identity.user.id, ReminderType.EVENING_DIGEST, now=now
    )
    await session.flush()
    await record_audit_event(
        session,
        actor_kind="pwa",
        action="settings.preferences_updated",
        outcome="success",
        user_id=identity.user.id,
        entity_kind="preferences",
        safe_metadata={"status": "updated"},
    )
    return _settings_payload(await _preferences(session, identity, settings), settings)


async def _settings_topics(
    session: AsyncSession,
    user_id: UUID,
    *,
    query: str | None,
    active: bool | None,
    limit: int,
    offset: int,
) -> PageResult:
    statement = select(Topic).where(Topic.user_id == user_id)
    if query and query.strip():
        statement = statement.where(Topic.name.ilike(f"%{query.strip()}%"))
    if active is not None:
        statement = statement.where(Topic.is_active.is_(active))
    values = list(
        await session.scalars(
            statement.order_by(Topic.name, Topic.id).offset(offset).limit(limit + 1)
        )
    )
    return PageResult(values[:limit], limit, offset, len(values) > limit)


async def _settings_people(
    session: AsyncSession,
    user_id: UUID,
    *,
    query: str | None,
    active: bool | None,
    limit: int,
    offset: int,
) -> PageResult:
    statement = select(Person).where(Person.user_id == user_id)
    if query and query.strip():
        statement = statement.where(Person.display_name.ilike(f"%{query.strip()}%"))
    if active is not None:
        statement = statement.where(Person.is_active.is_(active))
    values = list(
        await session.scalars(
            statement.order_by(Person.display_name, Person.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    return PageResult(values[:limit], limit, offset, len(values) > limit)


@router.get("/settings/topics")
async def settings_topics(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    q: str | None = None,
    active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    page = await _settings_topics(
        session,
        identity.user.id,
        query=q,
        active=active,
        limit=limit,
        offset=offset,
    )
    return {
        **_page_payload(page),
        "items": [_topic_payload(topic) for topic in page.items],
    }


@router.post("/topics", status_code=status.HTTP_201_CREATED)
async def create_pwa_topic(
    payload: TopicRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    try:
        topic = await create_topic(
            session,
            identity.user.id,
            payload.name,
            description=payload.description,
            aliases=payload.aliases,
        )
        await record_audit_event(
            session,
            actor_kind="pwa",
            action="topic.created",
            outcome="success",
            user_id=identity.user.id,
            entity_kind="topic",
            entity_id=topic.id,
        )
        return _topic_payload(topic)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.patch("/settings/topics/{topic_id}")
async def update_pwa_topic(
    topic_id: UUID,
    payload: TopicRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    topic = await session.scalar(
        select(Topic)
        .where(Topic.id == topic_id, Topic.user_id == identity.user.id)
        .with_for_update()
    )
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    topic.name = normalize_required_text(payload.name, "name")
    topic.description = normalize_optional_text(payload.description)
    topic.aliases = normalize_aliases(payload.aliases, topic.name)
    topic.is_active = payload.is_active
    await session.flush()
    await record_audit_event(
        session,
        actor_kind="pwa",
        action="topic.updated",
        outcome="success",
        user_id=identity.user.id,
        entity_kind="topic",
        entity_id=topic.id,
        safe_metadata={"status": "active" if topic.is_active else "inactive"},
    )
    return _topic_payload(topic)


@router.get("/settings/people")
async def settings_people(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    q: str | None = None,
    active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    page = await _settings_people(
        session,
        identity.user.id,
        query=q,
        active=active,
        limit=limit,
        offset=offset,
    )
    return {
        **_page_payload(page),
        "items": [_person_payload(person) for person in page.items],
    }


@router.post("/people", status_code=status.HTTP_201_CREATED)
async def create_pwa_person(
    payload: PersonRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    person = await create_person(
        session,
        identity.user.id,
        payload.display_name,
        role=payload.role,
        notes=payload.notes,
        aliases=payload.aliases,
    )
    await record_audit_event(
        session,
        actor_kind="pwa",
        action="person.created",
        outcome="success",
        user_id=identity.user.id,
        entity_kind="person",
        entity_id=person.id,
    )
    return _person_payload(person)


@router.patch("/settings/people/{person_id}")
async def update_pwa_person(
    person_id: UUID,
    payload: PersonRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[PwaIdentity, Depends(require_csrf)],
) -> dict[str, object]:
    person = await session.scalar(
        select(Person)
        .where(Person.id == person_id, Person.user_id == identity.user.id)
        .with_for_update()
    )
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    person.display_name = normalize_required_text(payload.display_name, "display_name")
    person.role = normalize_optional_text(payload.role)
    person.notes = normalize_optional_text(payload.notes)
    person.aliases = normalize_aliases(payload.aliases, person.display_name)
    person.is_active = payload.is_active
    await session.flush()
    await record_audit_event(
        session,
        actor_kind="pwa",
        action="person.updated",
        outcome="success",
        user_id=identity.user.id,
        entity_kind="person",
        entity_id=person.id,
        safe_metadata={"status": "active" if person.is_active else "inactive"},
    )
    return _person_payload(person)
