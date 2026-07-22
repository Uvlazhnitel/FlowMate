# ruff: noqa: RUF001
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.db.models import (
    DraftSession,
    Meeting,
    MeetingEvent,
    MeetingNote,
    MeetingParticipant,
    MeetingTopic,
    Note,
    Person,
    Topic,
    User,
)
from flowmate.meetings.enums import MeetingEventType, MeetingStatus, MeetingType
from flowmate.task_engine.service import (
    normalize_optional_text,
    normalize_required_text,
)


class InvalidMeetingTransitionError(ValueError):
    """The requested meeting transition is not allowed."""


class ActiveMeetingExistsError(InvalidMeetingTransitionError):
    """The user already has an active meeting."""


class StaleMeetingError(InvalidMeetingTransitionError):
    """The meeting changed after the client rendered it."""


TYPE_TITLES = {
    MeetingType.LEAD: "Встреча с руководителем",
    MeetingType.TEAM: "Командная встреча",
    MeetingType.CLIENT_SYNC: "Синхронизация с клиентом",
    MeetingType.STEERING: "Steering-встреча",
    MeetingType.ONE_TO_ONE: "Встреча один на один",
    MeetingType.OTHER: "Встреча",
}
LONG_RUNNING_AFTER = timedelta(hours=12)


@dataclass(frozen=True, slots=True)
class MeetingPage:
    items: list[dict[str, object]]
    limit: int
    offset: int
    has_more: bool


def meeting_now() -> datetime:
    return datetime.now(UTC)


def meeting_revision(value: datetime) -> int:
    normalized = value.astimezone(UTC)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = normalized - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def meeting_is_long_running(meeting: Meeting, *, now: datetime | None = None) -> bool:
    return bool(
        meeting.status == MeetingStatus.ACTIVE.value
        and meeting.started_at is not None
        and (now or meeting_now()) - meeting.started_at >= LONG_RUNNING_AFTER
    )


def default_meeting_title(
    meeting_type: MeetingType, now: datetime, timezone: ZoneInfo
) -> str:
    local = now.astimezone(timezone)
    return f"{TYPE_TITLES[meeting_type]} · {local:%d.%m.%Y}"


async def get_meeting(
    session: AsyncSession, user_id: UUID, meeting_id: UUID
) -> Meeting | None:
    return cast(
        Meeting | None,
        await session.scalar(
            select(Meeting).where(Meeting.id == meeting_id, Meeting.user_id == user_id)
        ),
    )


async def get_active_meeting(session: AsyncSession, user_id: UUID) -> Meeting | None:
    return cast(
        Meeting | None,
        await session.scalar(
            select(Meeting).where(
                Meeting.user_id == user_id,
                Meeting.status == MeetingStatus.ACTIVE.value,
            )
        ),
    )


async def get_recoverable_meeting(
    session: AsyncSession, user_id: UUID
) -> Meeting | None:
    active = await get_active_meeting(session, user_id)
    if active is not None:
        return active
    return cast(
        Meeting | None,
        await session.scalar(
            select(Meeting)
            .where(
                Meeting.user_id == user_id,
                Meeting.status.in_(
                    (
                        MeetingStatus.PROCESSING.value,
                        MeetingStatus.REVIEW_REQUIRED.value,
                    )
                ),
            )
            .order_by(Meeting.updated_at.desc(), Meeting.id.desc())
        ),
    )


async def _existing_event(
    session: AsyncSession,
    user_id: UUID,
    event_type: MeetingEventType,
    *,
    telegram_update_id: int | None,
    client_action_id: UUID | None,
) -> MeetingEvent | None:
    if telegram_update_id is None and client_action_id is None:
        return None
    origin = (
        MeetingEvent.telegram_update_id == telegram_update_id
        if telegram_update_id is not None
        else MeetingEvent.client_action_id == client_action_id
    )
    return cast(
        MeetingEvent | None,
        await session.scalar(
            select(MeetingEvent).where(
                MeetingEvent.user_id == user_id,
                MeetingEvent.event_type == event_type.value,
                origin,
            )
        ),
    )


async def _append_event(
    session: AsyncSession,
    meeting: Meeting,
    event_type: MeetingEventType,
    previous_status: str | None,
    *,
    telegram_update_id: int | None,
    client_action_id: UUID | None,
) -> MeetingEvent:
    event = MeetingEvent(
        user_id=meeting.user_id,
        meeting_id=meeting.id,
        event_type=event_type.value,
        previous_status=previous_status,
        new_status=meeting.status,
        telegram_update_id=telegram_update_id,
        client_action_id=client_action_id,
    )
    session.add(event)
    await session.flush()
    return event


async def create_meeting(
    session: AsyncSession,
    user_id: UUID,
    meeting_type: MeetingType,
    title: str,
    *,
    telegram_update_id: int | None = None,
    client_action_id: UUID | None = None,
    now: datetime | None = None,
) -> Meeting:
    duplicate = await _existing_event(
        session,
        user_id,
        MeetingEventType.CREATED,
        telegram_update_id=telegram_update_id,
        client_action_id=client_action_id,
    )
    if duplicate is not None:
        meeting = await get_meeting(session, user_id, duplicate.meeting_id)
        if meeting is None:
            raise ValueError("meeting not found")
        return meeting
    timestamp = now or meeting_now()
    meeting = Meeting(
        user_id=user_id,
        type=MeetingType(meeting_type).value,
        title=normalize_required_text(title, "title")[:500],
        status=MeetingStatus.PLANNED.value,
        updated_at=timestamp,
    )
    session.add(meeting)
    await session.flush()
    await _append_event(
        session,
        meeting,
        MeetingEventType.CREATED,
        None,
        telegram_update_id=telegram_update_id,
        client_action_id=client_action_id,
    )
    return meeting


async def _lock_meeting(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    expected_revision: int | None = None,
) -> Meeting:
    meeting = await session.scalar(
        select(Meeting)
        .where(Meeting.id == meeting_id, Meeting.user_id == user_id)
        .with_for_update()
    )
    if meeting is None:
        raise ValueError("meeting not found")
    if (
        expected_revision is not None
        and meeting_revision(meeting.updated_at) != expected_revision
    ):
        raise StaleMeetingError("meeting is stale")
    return meeting


async def start_meeting(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    *,
    expected_revision: int | None = None,
    telegram_update_id: int | None = None,
    client_action_id: UUID | None = None,
    now: datetime | None = None,
) -> Meeting:
    duplicate = await _existing_event(
        session,
        user_id,
        MeetingEventType.STARTED,
        telegram_update_id=telegram_update_id,
        client_action_id=client_action_id,
    )
    if duplicate is not None:
        meeting = await get_meeting(session, user_id, duplicate.meeting_id)
        if meeting is None:
            raise ValueError("meeting not found")
        return meeting
    await session.scalar(select(User).where(User.id == user_id).with_for_update())
    meeting = await _lock_meeting(session, user_id, meeting_id, expected_revision)
    if meeting.status != MeetingStatus.PLANNED.value:
        raise InvalidMeetingTransitionError("only planned meetings can start")
    if await get_active_meeting(session, user_id) is not None:
        raise ActiveMeetingExistsError("an active meeting already exists")
    timestamp = now or meeting_now()
    previous = meeting.status
    meeting.status = MeetingStatus.ACTIVE.value
    meeting.started_at = timestamp
    meeting.ended_at = None
    meeting.updated_at = timestamp
    await session.flush()
    await _append_event(
        session,
        meeting,
        MeetingEventType.STARTED,
        previous,
        telegram_update_id=telegram_update_id,
        client_action_id=client_action_id,
    )
    return meeting


async def end_meeting(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    *,
    expected_revision: int | None = None,
    telegram_update_id: int | None = None,
    client_action_id: UUID | None = None,
    now: datetime | None = None,
) -> Meeting:
    duplicate = await _existing_event(
        session,
        user_id,
        MeetingEventType.ENDED,
        telegram_update_id=telegram_update_id,
        client_action_id=client_action_id,
    )
    if duplicate is not None:
        meeting = await get_meeting(session, user_id, duplicate.meeting_id)
        if meeting is None:
            raise ValueError("meeting not found")
        return meeting
    meeting = await _lock_meeting(session, user_id, meeting_id, expected_revision)
    if meeting.status != MeetingStatus.ACTIVE.value:
        raise InvalidMeetingTransitionError("only active meetings can end")
    timestamp = now or meeting_now()
    previous = meeting.status
    meeting.status = MeetingStatus.PROCESSING.value
    meeting.ended_at = timestamp
    meeting.updated_at = timestamp
    await session.flush()
    await _append_event(
        session,
        meeting,
        MeetingEventType.ENDED,
        previous,
        telegram_update_id=telegram_update_id,
        client_action_id=client_action_id,
    )
    return meeting


async def cancel_meeting(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    *,
    expected_revision: int | None = None,
    telegram_update_id: int | None = None,
    client_action_id: UUID | None = None,
    now: datetime | None = None,
) -> Meeting:
    duplicate = await _existing_event(
        session,
        user_id,
        MeetingEventType.CANCELLED,
        telegram_update_id=telegram_update_id,
        client_action_id=client_action_id,
    )
    if duplicate is not None:
        meeting = await get_meeting(session, user_id, duplicate.meeting_id)
        if meeting is None:
            raise ValueError("meeting not found")
        return meeting
    meeting = await _lock_meeting(session, user_id, meeting_id, expected_revision)
    if meeting.status not in {MeetingStatus.PLANNED.value, MeetingStatus.ACTIVE.value}:
        raise InvalidMeetingTransitionError("meeting cannot be cancelled")
    timestamp = now or meeting_now()
    previous = meeting.status
    meeting.status = MeetingStatus.CANCELLED.value
    if previous == MeetingStatus.ACTIVE.value:
        meeting.ended_at = timestamp
    meeting.updated_at = timestamp
    await session.flush()
    await _append_event(
        session,
        meeting,
        MeetingEventType.CANCELLED,
        previous,
        telegram_update_id=telegram_update_id,
        client_action_id=client_action_id,
    )
    return meeting


async def add_participant(
    session: AsyncSession, user_id: UUID, meeting_id: UUID, person_id: UUID
) -> bool:
    await _lock_meeting(session, user_id, meeting_id)
    person = await session.scalar(
        select(Person).where(
            Person.id == person_id,
            Person.user_id == user_id,
            Person.is_active.is_(True),
        )
    )
    if person is None:
        raise ValueError("person not found")
    existing = await session.scalar(
        select(MeetingParticipant).where(
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.person_id == person_id,
        )
    )
    if existing is not None:
        return False
    session.add(
        MeetingParticipant(user_id=user_id, meeting_id=meeting_id, person_id=person_id)
    )
    await session.flush()
    return True


async def remove_participant(
    session: AsyncSession, user_id: UUID, meeting_id: UUID, person_id: UUID
) -> bool:
    await _lock_meeting(session, user_id, meeting_id)
    association = await session.scalar(
        select(MeetingParticipant).where(
            MeetingParticipant.user_id == user_id,
            MeetingParticipant.meeting_id == meeting_id,
            MeetingParticipant.person_id == person_id,
        )
    )
    if association is None:
        return False
    await session.delete(association)
    await session.flush()
    return True


async def link_topic(
    session: AsyncSession,
    user_id: UUID,
    meeting_id: UUID,
    topic_id: UUID,
    *,
    primary: bool = False,
) -> bool:
    meeting = await _lock_meeting(session, user_id, meeting_id)
    topic = await session.scalar(
        select(Topic).where(
            Topic.id == topic_id, Topic.user_id == user_id, Topic.is_active.is_(True)
        )
    )
    if topic is None:
        raise ValueError("topic not found")
    existing = await session.scalar(
        select(MeetingTopic).where(
            MeetingTopic.meeting_id == meeting_id, MeetingTopic.topic_id == topic_id
        )
    )
    changed = existing is None
    if changed:
        session.add(
            MeetingTopic(user_id=user_id, meeting_id=meeting_id, topic_id=topic_id)
        )
    if primary:
        meeting.primary_topic_id = topic_id
        changed = True
    await session.flush()
    return changed


async def link_note_to_active_meeting(
    session: AsyncSession, user_id: UUID, note: Note
) -> Meeting | None:
    if note.user_id != user_id:
        raise ValueError("note not found")
    meeting = await get_active_meeting(session, user_id)
    if meeting is None:
        return None
    existing = await session.scalar(
        select(MeetingNote).where(MeetingNote.note_id == note.id)
    )
    if existing is None:
        session.add(
            MeetingNote(user_id=user_id, meeting_id=meeting.id, note_id=note.id)
        )
        await session.flush()
    return meeting


async def serialize_meeting(
    session: AsyncSession, meeting: Meeting
) -> dict[str, object]:
    participant_rows = (
        await session.execute(
            select(Person.id, Person.display_name)
            .join(MeetingParticipant, MeetingParticipant.person_id == Person.id)
            .where(
                MeetingParticipant.meeting_id == meeting.id,
                MeetingParticipant.user_id == meeting.user_id,
            )
            .order_by(Person.display_name)
        )
    ).all()
    participants = [(person_id, name) for person_id, name in participant_rows]
    topic_rows = (
        await session.execute(
            select(Topic.id, Topic.name)
            .join(MeetingTopic, MeetingTopic.topic_id == Topic.id)
            .where(
                MeetingTopic.meeting_id == meeting.id,
                MeetingTopic.user_id == meeting.user_id,
            )
            .order_by(Topic.name)
        )
    ).all()
    topics = [(topic_id, name) for topic_id, name in topic_rows]
    note_count = await session.scalar(
        select(func.count(DraftSession.id)).where(
            DraftSession.meeting_id == meeting.id,
            DraftSession.user_id == meeting.user_id,
            DraftSession.capture_review_status != "removed",
        )
    )
    return {
        "id": meeting.id,
        "title": meeting.title,
        "type": meeting.type,
        "status": meeting.status,
        "started_at": meeting.started_at,
        "ended_at": meeting.ended_at,
        "summary": normalize_optional_text(meeting.summary),
        "primary_topic_id": meeting.primary_topic_id,
        "participants": participants,
        "topics": topics,
        "captured_note_count": int(note_count or 0),
        "long_running": meeting_is_long_running(meeting),
        "created_at": meeting.created_at,
        "updated_at": meeting.updated_at,
        "revision": meeting_revision(meeting.updated_at),
    }


async def list_recent_meetings(
    session: AsyncSession, user_id: UUID, *, limit: int, offset: int
) -> MeetingPage:
    if not 1 <= limit <= 50 or offset < 0:
        raise ValueError("invalid pagination")
    rows = list(
        await session.scalars(
            select(Meeting)
            .where(
                Meeting.user_id == user_id, Meeting.status != MeetingStatus.ACTIVE.value
            )
            .order_by(Meeting.updated_at.desc(), Meeting.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
    )
    items = [await serialize_meeting(session, value) for value in rows[:limit]]
    return MeetingPage(items, limit, offset, len(rows) > limit)
