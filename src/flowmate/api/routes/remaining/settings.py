from datetime import UTC, datetime, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.api.dependencies import get_session
from flowmate.auth.dependencies import PwaIdentity, require_csrf, require_pwa_session
from flowmate.core.config import Settings, get_settings
from flowmate.db.models import Person, Topic
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
from flowmate.stabilization.audit import record_audit_event
from flowmate.task_engine.operational import PageResult
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    normalize_aliases,
    normalize_optional_text,
    normalize_required_text,
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


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PreferencesRequest(StrictRequest):
    timezone: str = Field(min_length=1, max_length=64)
    morning_digest_enabled: bool
    morning_digest_time: time
    evening_digest_enabled: bool
    evening_digest_time: time
    quiet_hours_enabled: bool
    quiet_hours_start: time
    quiet_hours_end: time
    default_reminder_time: time
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


async def _preferences(
    session: AsyncSession, identity: PwaIdentity, settings: Settings
) -> EffectiveNotificationPreferences:
    return await get_effective_notification_preferences(
        session,
        identity.user.id,
        NotificationDefaults.from_settings(settings),
    )


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
        reminder_time = validate_clock_time(payload.default_reminder_time)
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
    value.default_reminder_time = reminder_time
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
