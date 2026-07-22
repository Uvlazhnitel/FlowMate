from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from flowmate.ai.schemas import (
    DraftItemType,
    MeetingAgendaSuggestion,
    MeetingReviewParseResult,
    MeetingReviewProposal,
)
from flowmate.api.app import create_app
from flowmate.db.drafts import create_parsing_draft, get_draft_for_user
from flowmate.db.models import (
    DraftItemPerson,
    MeetingAgendaEntry,
    MeetingEvent,
    MeetingNote,
    MeetingReviewItem,
    MeetingWorkItem,
    Reminder,
    User,
    WorkItem,
)
from flowmate.db.notes import create_note_idempotently
from flowmate.db.users import create_telegram_user
from flowmate.meetings.capture import (
    CaptureConflictError,
    capture_revision,
    create_capture,
    edit_capture_item,
    list_captures,
    remove_capture,
    save_capture_analysis,
    serialize_capture,
)
from flowmate.meetings.enums import MeetingStatus, MeetingType
from flowmate.meetings.review import (
    confirm_review,
    generate_review,
    review_revision,
    set_agenda_outcome,
    set_review_item_action,
    sync_review_capture_item,
)
from flowmate.meetings.service import (
    ActiveMeetingExistsError,
    add_participant,
    cancel_meeting,
    create_meeting,
    end_meeting,
    get_active_meeting,
    get_recoverable_meeting,
    link_note_to_active_meeting,
    link_topic,
    meeting_is_long_running,
    remove_participant,
    start_meeting,
)
from flowmate.meetings.setup import claim_setup_update, open_setup
from flowmate.task_engine.conversion import DraftConversionService
from flowmate.task_engine.enums import WorkItemPriority, WorkItemStatus, WorkItemType
from flowmate.task_engine.remaining import DraftItemEdit
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
)
from tests.ai_factories import (
    make_analysis_result,
    make_draft_item,
    make_parse_result,
    make_temporal_candidate,
)
from tests.conftest import started_app
from tests.integration.test_pwa_auth import (
    ORIGIN,
    TELEGRAM_USER_ID,
    CapturingLoginCodeSender,
    auth_settings,
)
from tests.integration.test_pwa_operations import authenticated_client

OTHER_USER_ID = TELEGRAM_USER_ID + 1_700


class MeetingReviewStub:
    def __init__(self, result: MeetingReviewParseResult) -> None:
        self.result = result
        self.calls = 0

    async def parse_meeting_review(
        self, *, system_prompt: str, user_text: str
    ) -> MeetingReviewParseResult:
        assert "never claim to create" in system_prompt
        assert '"captures"' in user_text
        self.calls += 1
        return self.result


@pytest.fixture(autouse=True)
async def cleanup_meeting_users(database_engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with AsyncSession(database_engine) as session:
        await session.execute(
            delete(User).where(
                User.telegram_user_id.in_((TELEGRAM_USER_ID, OTHER_USER_ID))
            )
        )
        await session.commit()


@pytest.mark.integration
async def test_meeting_domain_transitions_context_and_isolation(
    database_engine: AsyncEngine,
) -> None:
    now = datetime(2026, 8, 1, 10, tzinfo=UTC)
    async with AsyncSession(database_engine) as session:
        user = await create_telegram_user(session, TELEGRAM_USER_ID)
        other = await create_telegram_user(session, OTHER_USER_ID)
        person = await create_person(session, user.id, "Anna")
        topic = await create_topic(session, user.id, "Launch")
        foreign_person = await create_person(session, other.id, "Private")
        meeting = await create_meeting(
            session, user.id, MeetingType.TEAM, "Weekly", now=now
        )
        assert meeting.status == MeetingStatus.PLANNED.value
        assert await add_participant(session, user.id, meeting.id, person.id)
        assert not await add_participant(session, user.id, meeting.id, person.id)
        assert await link_topic(session, user.id, meeting.id, topic.id, primary=True)
        with pytest.raises(ValueError, match="person not found"):
            await add_participant(session, user.id, meeting.id, foreign_person.id)
        meeting = await start_meeting(
            session, user.id, meeting.id, now=now + timedelta(minutes=1)
        )
        assert await get_active_meeting(session, user.id) == meeting
        second = await create_meeting(
            session, user.id, MeetingType.OTHER, "Second", now=now
        )
        with pytest.raises(ActiveMeetingExistsError):
            await start_meeting(session, user.id, second.id, now=now)
        note, created = await create_note_idempotently(
            session,
            user_id=user.id,
            content="Meeting fact",
            source="text",
            telegram_update_id=991_001,
        )
        assert created
        assert await link_note_to_active_meeting(session, user.id, note) == meeting
        assert await link_note_to_active_meeting(session, user.id, note) == meeting
        assert (
            await session.scalar(
                select(func.count(MeetingNote.id)).where(MeetingNote.note_id == note.id)
            )
            == 1
        )
        assert await remove_participant(session, user.id, meeting.id, person.id)
        meeting = await end_meeting(
            session, user.id, meeting.id, now=now + timedelta(hours=1)
        )
        assert meeting.status == MeetingStatus.PROCESSING.value
        assert meeting.ended_at == now + timedelta(hours=1)
        await cancel_meeting(session, user.id, second.id, now=now)
        events = list(
            await session.scalars(
                select(MeetingEvent)
                .where(MeetingEvent.user_id == user.id)
                .order_by(MeetingEvent.created_at)
            )
        )
        assert {event.event_type for event in events} >= {
            "created",
            "started",
            "ended",
            "cancelled",
        }
        await session.commit()


@pytest.mark.integration
async def test_meeting_setup_callbacks_are_deduplicated(
    database_engine: AsyncEngine,
) -> None:
    async with AsyncSession(database_engine) as session:
        user = await create_telegram_user(session, TELEGRAM_USER_ID)
        setup = await open_setup(session, user.id, ttl_minutes=15)

        assert await claim_setup_update(session, setup, 991_101)
        assert not await claim_setup_update(session, setup, 991_101)
        assert await claim_setup_update(session, setup, 991_102)
        await session.commit()


@pytest.mark.integration
async def test_meeting_capture_context_sequence_analysis_and_undo(
    database_engine: AsyncEngine,
) -> None:
    now = datetime(2026, 8, 2, 9, tzinfo=UTC)
    async with AsyncSession(database_engine) as session:
        user = await create_telegram_user(session, TELEGRAM_USER_ID)
        person = await create_person(session, user.id, "Anna")
        topic = await create_topic(session, user.id, "Launch")
        meeting = await create_meeting(session, user.id, MeetingType.TEAM, "Weekly")
        await add_participant(session, user.id, meeting.id, person.id)
        await link_topic(session, user.id, meeting.id, topic.id, primary=True)
        await start_meeting(session, user.id, meeting.id, now=now)
        first_note, _ = await create_note_idempotently(
            session,
            user_id=user.id,
            content="Anna prepares the launch plan and note the open question",
            source="text",
            telegram_update_id=991_201,
        )
        first, created = await create_capture(
            session,
            user_id=user.id,
            meeting_id=meeting.id,
            note=first_note,
            timezone="Europe/Riga",
            captured_at=now,
            draft_ttl_hours=24,
        )
        duplicate, duplicate_created = await create_capture(
            session,
            user_id=user.id,
            meeting_id=meeting.id,
            note=first_note,
            timezone="UTC",
            captured_at=now,
            draft_ttl_hours=24,
        )
        assert duplicate.id == first.id
        assert created and not duplicate_created
        assert first.capture_sequence == 1
        assert first.capture_context["meeting_type"] == "team"
        assert first.capture_context["timezone"] == "Europe/Riga"
        assert first.capture_context["participants"][0]["name"] == "Anna"
        analysis = make_analysis_result(
            make_parse_result(
                [
                    make_draft_item(
                        title="Prepare launch plan",
                        person_candidates=["Anna"],
                        topic_candidates=["Launch"],
                    ),
                    make_draft_item(
                        type=DraftItemType.QUESTION,
                        title="Clarify launch owner",
                        missing_fields=["person"],
                        confidence=0.7,
                    ),
                ],
                confidence=0.78,
            )
        )
        await save_capture_analysis(
            session, first, analysis, draft_ttl_hours=24, now=now
        )
        assert await get_draft_for_user(session, first.id, user.id) is None
        assert len(first.items) == 2
        assert first.overall_confidence == 0.78
        assert first.current_question is not None
        assert first.items[0].selected_topic_id == topic.id
        assert (
            await session.scalar(
                select(func.count(DraftItemPerson.id)).where(
                    DraftItemPerson.draft_item_id == first.items[0].id
                )
            )
            == 1
        )
        first_revision = capture_revision(first)
        await edit_capture_item(
            session,
            user.id,
            meeting.id,
            first.id,
            first.items[0].id,
            DraftItemEdit(
                item_type=DraftItemType.TASK,
                title="Edited launch plan",
                description=None,
                priority=WorkItemPriority.HIGH,
                topic_id=topic.id,
                person_ids=(person.id,),
                due_at=None,
            ),
            expected_revision=first_revision,
            high_threshold=0.85,
            clarification_threshold=0.6,
            draft_ttl_hours=24,
            now=now + timedelta(seconds=1),
        )
        assert first.capture_review_status == "edited"
        assert first.current_question is None
        serialized = await serialize_capture(session, first)
        assert serialized["items"][0]["title"] == "Edited launch plan"
        second_note, _ = await create_note_idempotently(
            session,
            user_id=user.id,
            content="Second point",
            source="voice",
            telegram_update_id=991_202,
        )
        second, _ = await create_capture(
            session,
            user_id=user.id,
            meeting_id=meeting.id,
            note=second_note,
            timezone="Europe/Riga",
            captured_at=now + timedelta(minutes=1),
            draft_ttl_hours=24,
        )
        assert second.capture_sequence == 2
        ordinary_note, _ = await create_note_idempotently(
            session,
            user_id=user.id,
            content="Ordinary draft",
            source="text",
            telegram_update_id=991_203,
        )
        ordinary = await create_parsing_draft(
            session,
            user_id=user.id,
            source_note_id=ordinary_note.id,
            ttl_hours=24,
        )
        assert ordinary.meeting_id is None
        with pytest.raises(CaptureConflictError, match="latest"):
            await remove_capture(
                session, user.id, meeting.id, first.id, latest_only=True
            )
        await remove_capture(session, user.id, meeting.id, second.id, latest_only=True)
        page = await list_captures(session, user.id, meeting.id, limit=20, offset=0)
        assert [item["sequence"] for item in page.items] == [1]
        assert meeting_is_long_running(meeting, now=now + timedelta(hours=13))
        user_id = user.id
        meeting_id = meeting.id
        await session.commit()
    async with AsyncSession(database_engine) as restarted_session:
        recovered = await get_recoverable_meeting(restarted_session, user_id)
        assert recovered is not None
        assert recovered.id == meeting_id
        recovered_page = await list_captures(
            restarted_session, user_id, meeting_id, limit=20, offset=0
        )
        assert [item["sequence"] for item in recovered_page.items] == [1]


@pytest.mark.integration
async def test_meeting_review_conversion_agenda_planner_and_isolation(
    database_engine: AsyncEngine,
) -> None:
    now = datetime(2026, 8, 4, 10, tzinfo=UTC)
    async with AsyncSession(database_engine) as session:
        user = await create_telegram_user(session, TELEGRAM_USER_ID)
        other = await create_telegram_user(session, OTHER_USER_ID)
        person = await create_person(session, user.id, "Anna")
        topic = await create_topic(session, user.id, "Launch")
        agenda_item = await create_work_item(
            session,
            user.id,
            item_type=WorkItemType.QUESTION,
            title="Who owns launch?",
            status=WorkItemStatus.ACTIVE,
            topic_id=topic.id,
        )
        await link_person_to_work_item(session, user.id, agenda_item.id, person.id)
        meeting = await create_meeting(session, user.id, MeetingType.TEAM, "Review")
        await add_participant(session, user.id, meeting.id, person.id)
        await link_topic(session, user.id, meeting.id, topic.id, primary=True)
        await start_meeting(session, user.id, meeting.id, now=now)
        note, _ = await create_note_idempotently(
            session,
            user_id=user.id,
            content="Anna will prepare launch plan. We chose option A.",
            source="text",
            telegram_update_id=991_301,
        )
        capture, _ = await create_capture(
            session,
            user_id=user.id,
            meeting_id=meeting.id,
            note=note,
            timezone="UTC",
            captured_at=now,
            draft_ttl_hours=24,
        )
        await save_capture_analysis(
            session,
            capture,
            make_analysis_result(
                make_parse_result(
                    [
                        make_draft_item(
                            title="Prepare launch plan",
                            topic_candidates=["Launch"],
                            person_candidates=["Anna"],
                            due_date_candidate=make_temporal_candidate(),
                        ),
                        make_draft_item(
                            type=DraftItemType.DECISION,
                            title="Use option A",
                            topic_candidates=["Launch"],
                        ),
                    ]
                )
            ),
            draft_ttl_hours=24,
            now=now,
        )
        await end_meeting(session, user.id, meeting.id, now=now + timedelta(hours=1))
        result = MeetingReviewParseResult(
            summary="Launch plan and option A agreed.",
            proposals=[
                MeetingReviewProposal(
                    source_capture_id=capture.id,
                    source_draft_item_id=capture.items[0].id,
                    category="task",
                    item=make_draft_item(
                        title="Prepare launch plan",
                        topic_candidates=["Launch"],
                        person_candidates=["Anna"],
                        due_date_candidate=make_temporal_candidate(),
                    ),
                    suggested_next_action="Anna prepares the plan",
                ),
                MeetingReviewProposal(
                    source_capture_id=capture.id,
                    source_draft_item_id=capture.items[1].id,
                    category="decision",
                    item=make_draft_item(
                        type=DraftItemType.DECISION,
                        title="Use option A",
                        topic_candidates=["Launch"],
                    ),
                    consequences=["Plan follows option A"],
                    related_proposal_numbers=[1],
                ),
            ],
            agenda=[
                MeetingAgendaSuggestion(
                    work_item_id=agenda_item.id,
                    outcome="answered",
                    result="Anna owns launch",
                )
            ],
            suggested_next_actions=["Prepare the plan"],
        )
        provider = MeetingReviewStub(result)
        review = await generate_review(
            session,
            user.id,
            meeting.id,
            provider,
            high_threshold=0.8,
            clarification_threshold=0.5,
            now=now + timedelta(hours=1),
        )
        assert review.status == "review_required"
        assert provider.calls == 1
        assert (
            await generate_review(
                session,
                user.id,
                meeting.id,
                provider,
                high_threshold=0.8,
                clarification_threshold=0.5,
            )
        ).id == review.id
        assert provider.calls == 1
        await edit_capture_item(
            session,
            user.id,
            meeting.id,
            capture.id,
            capture.items[0].id,
            DraftItemEdit(
                item_type=DraftItemType.TASK,
                title="Prepare revised launch plan",
                description=None,
                priority=WorkItemPriority.HIGH,
                topic_id=topic.id,
                person_ids=(person.id,),
                due_at=now + timedelta(days=7),
            ),
            expected_revision=capture_revision(capture),
            high_threshold=0.8,
            clarification_threshold=0.5,
            draft_ttl_hours=24,
            now=now + timedelta(hours=1, seconds=1),
        )
        await sync_review_capture_item(
            session, user.id, meeting.id, capture.items[0].id
        )
        task_review_item = await session.scalar(
            select(MeetingReviewItem).where(
                MeetingReviewItem.review_id == review.id,
                MeetingReviewItem.category == "task",
            )
        )
        assert task_review_item is not None
        assert task_review_item.title == "Prepare revised launch plan"
        assert task_review_item.status == "ready"
        await set_review_item_action(
            session,
            user.id,
            meeting.id,
            task_review_item.id,
            action="planner_on",
            expected_revision=review_revision(review),
        )
        agenda_entry = await session.scalar(
            select(MeetingAgendaEntry).where(
                MeetingAgendaEntry.meeting_id == meeting.id
            )
        )
        assert agenda_entry is not None
        await set_agenda_outcome(
            session,
            user.id,
            meeting.id,
            agenda_entry.id,
            "answered",
            "Anna owns launch",
        )
        confirmation_id = uuid4()
        confirmation = await confirm_review(
            session,
            user.id,
            meeting.id,
            expected_revision=review_revision(review),
            client_action_id=confirmation_id,
            move_incomplete_to_inbox=False,
            conversion_service=DraftConversionService(),
            now=now + timedelta(hours=1, minutes=1),
        )
        assert confirmation.completed
        assert len(confirmation.converted_ids) == 2
        repeated = await confirm_review(
            session,
            user.id,
            meeting.id,
            expected_revision=0,
            client_action_id=confirmation_id,
            move_incomplete_to_inbox=False,
            conversion_service=DraftConversionService(),
        )
        assert repeated.converted_ids == confirmation.converted_ids
        results = list(
            await session.scalars(
                select(WorkItem).where(WorkItem.id.in_(confirmation.converted_ids))
            )
        )
        task = next(item for item in results if item.type == "task")
        decision = next(item for item in results if item.type == "decision")
        assert task.planner_status == "needs_transfer"
        assert decision.status == "done"
        assert agenda_item.status == "done"
        assert (
            await session.scalar(
                select(func.count(Reminder.id)).where(Reminder.work_item_id == task.id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(MeetingWorkItem.id)).where(
                    MeetingWorkItem.meeting_id == meeting.id
                )
            )
            == 3
        )
        with pytest.raises(ValueError, match="not ready"):
            await generate_review(
                session,
                other.id,
                meeting.id,
                provider,
                high_threshold=0.8,
                clarification_threshold=0.5,
            )
        await session.commit()


@pytest.mark.integration
async def test_meeting_api_auth_idempotency_actions_and_isolation(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC", app_debug=True),
        engine=database_engine,
        login_code_sender=sender,
    )
    async with started_app(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/api/v1/meetings/active")).status_code == 401
            csrf = await authenticated_client(client, sender)
            async with AsyncSession(database_engine) as session:
                user = await session.scalar(
                    select(User).where(User.telegram_user_id == TELEGRAM_USER_ID)
                )
                assert user is not None
                person = await create_person(session, user.id, "Anna")
                topic = await create_topic(session, user.id, "Launch")
                other = await create_telegram_user(session, OTHER_USER_ID)
                foreign = await create_meeting(
                    session, other.id, MeetingType.OTHER, "Private"
                )
                person_id = person.id
                topic_id = topic.id
                foreign_id = foreign.id
                await session.commit()

            payload = {
                "client_action_id": str(uuid4()),
                "type": "team",
                "title": None,
                "participant_ids": [str(person_id)],
                "topic_ids": [str(topic_id)],
                "primary_topic_id": str(topic_id),
            }
            rejected = await client.post(
                "/api/v1/meetings", headers={"Origin": ORIGIN}, json=payload
            )
            assert rejected.status_code == 403
            headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
            created = await client.post(
                "/api/v1/meetings", headers=headers, json=payload
            )
            assert created.status_code == 200, created.text
            card = created.json()["meeting"]
            assert card["status"] == "active"
            assert card["long_running"] is False
            assert card["participants"][0][1] == "Anna"
            assert card["primary_topic_id"] == str(topic_id)
            duplicate = await client.post(
                "/api/v1/meetings", headers=headers, json=payload
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["meeting"]["id"] == card["id"]
            async with AsyncSession(database_engine) as session:
                user = await session.scalar(
                    select(User).where(User.telegram_user_id == TELEGRAM_USER_ID)
                )
                assert user is not None
                note, _ = await create_note_idempotently(
                    session,
                    user_id=user.id,
                    content="API capture",
                    source="text",
                    telegram_update_id=991_204,
                )
                capture, _ = await create_capture(
                    session,
                    user_id=user.id,
                    meeting_id=UUID(card["id"]),
                    note=note,
                    timezone="UTC",
                    captured_at=datetime.now(UTC),
                    draft_ttl_hours=24,
                )
                await save_capture_analysis(
                    session,
                    capture,
                    make_analysis_result(),
                    draft_ttl_hours=24,
                )
                capture_id = capture.id
                item_id = capture.items[0].id
                revision = capture_revision(capture)
                await session.commit()
            captures = await client.get(f"/api/v1/meetings/{card['id']}/captures")
            assert captures.status_code == 200
            assert captures.json()["items"][0]["source_text"] == "API capture"
            assert "raw_payload" not in captures.text
            edited = await client.patch(
                f"/api/v1/meetings/{card['id']}/captures/{capture_id}/items/{item_id}",
                headers=headers,
                json={
                    "expected_revision": revision,
                    "item_type": "task",
                    "title": "Edited capture",
                    "priority": "high",
                },
            )
            assert edited.status_code == 200, edited.text
            edited_capture = edited.json()["capture"]
            assert edited_capture["review_status"] == "edited"
            removed = await client.post(
                f"/api/v1/meetings/{card['id']}/captures/{capture_id}/actions",
                headers=headers,
                json={
                    "action": "remove",
                    "client_action_id": str(uuid4()),
                    "expected_revision": edited_capture["revision"],
                },
            )
            assert removed.status_code == 200, removed.text
            assert removed.json()["capture"]["review_status"] == "removed"
            hidden_captures = await client.get(
                f"/api/v1/meetings/{foreign_id}/captures"
            )
            assert hidden_captures.status_code == 404
            hidden = await client.post(
                f"/api/v1/meetings/{foreign_id}/actions",
                headers=headers,
                json={
                    "action": "cancel",
                    "client_action_id": str(uuid4()),
                    "expected_revision": 0,
                },
            )
            assert hidden.status_code == 404
            ended = await client.post(
                f"/api/v1/meetings/{card['id']}/actions",
                headers=headers,
                json={
                    "action": "end",
                    "client_action_id": str(uuid4()),
                    "expected_revision": card["revision"],
                },
            )
            assert ended.status_code == 200, ended.text
            assert ended.json()["meeting"]["status"] == "review_required"
            assert (await client.get("/api/v1/meetings/active")).json()[
                "meeting"
            ] is None
            recent = await client.get("/api/v1/meetings?limit=1")
            assert recent.status_code == 200
            assert recent.json()["items"][0]["id"] == card["id"]
