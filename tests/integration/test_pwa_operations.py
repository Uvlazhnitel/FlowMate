from collections.abc import AsyncIterator
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from flowmate.api.app import create_app
from flowmate.db.models import (
    NoteLink,
    Reminder,
    User,
    WorkItem,
    WorkItemEvent,
    WorkItemRelation,
)
from flowmate.db.users import create_telegram_user
from flowmate.reminders.enums import ReminderStatus
from flowmate.reminders.preferences import NotificationDefaults, effective_preferences
from flowmate.task_engine.operational import TodaySection, list_today_section
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
)
from tests.conftest import started_app
from tests.integration.test_pwa_auth import (
    ORIGIN,
    TELEGRAM_USER_ID,
    CapturingLoginCodeSender,
    auth_settings,
)


@pytest.fixture(autouse=True)
async def cleanup_operational_users(
    database_engine: AsyncEngine,
) -> AsyncIterator[None]:
    yield
    async with AsyncSession(database_engine) as session:
        await session.execute(
            delete(User).where(
                User.telegram_user_id.in_((TELEGRAM_USER_ID, TELEGRAM_USER_ID + 900))
            )
        )
        await session.commit()


async def authenticated_client(
    client: AsyncClient, sender: CapturingLoginCodeSender
) -> str:
    requested = await client.post("/api/v1/auth/login-code", headers={"Origin": ORIGIN})
    assert requested.status_code == 202
    authenticated = await client.post(
        "/api/v1/auth/session",
        headers={"Origin": ORIGIN},
        json={"code": sender.codes[-1]},
    )
    assert authenticated.status_code == 200
    csrf = client.cookies.get("flowmate_csrf")
    assert csrf is not None
    return csrf


@pytest.mark.integration
async def test_pwa_workspace_switch_changes_operational_scope(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
    )
    due_at = datetime.now(UTC) + timedelta(hours=1)
    async with started_app(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            csrf = await authenticated_client(client, sender)
            async with AsyncSession(database_engine) as session:
                user = await session.scalar(
                    select(User).where(User.telegram_user_id == TELEGRAM_USER_ID)
                )
                assert user is not None
                session.add_all(
                    [
                        WorkItem(
                            user_id=user.id,
                            workspace="personal",
                            type="task",
                            title="Personal only",
                            status="active",
                            priority="normal",
                            planner_status="needs_transfer",
                            due_at=due_at,
                        ),
                        WorkItem(
                            user_id=user.id,
                            workspace="work",
                            type="task",
                            title="Work only",
                            status="active",
                            priority="normal",
                            planner_status="needs_transfer",
                            due_at=due_at,
                        ),
                    ]
                )
                await session.commit()

            personal = await client.get("/api/v1/today?section=due_today")
            assert [item["title"] for item in personal.json()["items"]] == [
                "Personal only"
            ]

            switched = await client.put(
                "/api/v1/workspace",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={"workspace": "work"},
            )
            assert switched.status_code == 200
            assert switched.json()["active_workspace"] == "work"
            assert (await client.get("/api/v1/auth/me")).json()[
                "active_workspace"
            ] == "work"
            work = await client.get("/api/v1/today?section=due_today")
            assert [item["title"] for item in work.json()["items"]] == ["Work only"]


@pytest.mark.integration
async def test_operational_views_actions_and_user_isolation(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC", app_debug=True),
        engine=database_engine,
        login_code_sender=sender,
    )
    now = datetime.now(UTC)
    async with started_app(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/v1/dashboard")).status_code == 401
            csrf = await authenticated_client(client, sender)

            async with AsyncSession(database_engine) as session:
                user = await session.scalar(
                    select(User).where(User.telegram_user_id == TELEGRAM_USER_ID)
                )
                assert user is not None
                topic = await create_topic(session, user.id, "Launch")
                person = await create_person(session, user.id, "Anna", role="Owner")
                one_off = await create_person(session, user.id, "One-off contact")
                overdue = await create_work_item(
                    session,
                    user.id,
                    item_type="task",
                    title="Prepare launch",
                    topic_id=topic.id,
                    due_at=now - timedelta(days=2),
                )
                follow_up = await create_work_item(
                    session,
                    user.id,
                    item_type="follow_up",
                    title="Ask Anna",
                    topic_id=topic.id,
                    next_follow_up_at=now,
                )
                agenda = await create_work_item(
                    session,
                    user.id,
                    item_type="agenda_item",
                    title="Discuss launch",
                    topic_id=topic.id,
                )
                for item in (follow_up, agenda):
                    await link_person_to_work_item(session, user.id, item.id, person.id)
                other = await create_telegram_user(session, TELEGRAM_USER_ID + 900)
                foreign_topic = await create_topic(session, other.id, "Private topic")
                foreign_person = await create_person(
                    session, other.id, "Private person"
                )
                foreign = await create_work_item(
                    session,
                    other.id,
                    item_type="question",
                    title="Private",
                    topic_id=foreign_topic.id,
                )
                await link_person_to_work_item(
                    session, other.id, foreign.id, foreign_person.id
                )
                overdue_id = overdue.id
                topic_id = topic.id
                person_id = person.id
                one_off_id = one_off.id
                agenda_id = agenda.id
                foreign_topic_id = foreign_topic.id
                foreign_person_id = foreign_person.id
                foreign_id = foreign.id
                await session.commit()

            dashboard = await client.get("/api/v1/dashboard")
            assert dashboard.status_code == 200
            assert dashboard.json()["summary"] == {
                "overdue": 1,
                "due_today": 0,
                "follow_ups": 1,
                "waiting_overdue": 0,
                "questions": 0,
                "inbox": 3,
                "planner_queue": 2,
            }
            assert dashboard.json()["recommended"][0]["title"] == "Prepare launch"
            assert len(dashboard.json()["activity"]) == 3

            today = await client.get("/api/v1/today?section=overdue")
            assert [item["id"] for item in today.json()["items"]] == [str(overdue_id)]
            follow_ups = await client.get("/api/v1/today?section=follow_ups")
            assert follow_ups.json()["items"][0]["title"] == "Ask Anna"

            topics = await client.get("/api/v1/topics")
            assert topics.json()["items"][0]["open_count"] == 3
            topic_details = await client.get(f"/api/v1/topics/{topic_id}")
            assert topic_details.json()["name"] == "Launch"
            topic_active = await client.get(
                f"/api/v1/topics/{topic_id}/content?section=active&limit=1"
            )
            assert topic_active.json()["has_more"] is True
            topic_people = await client.get(
                f"/api/v1/topics/{topic_id}/content?section=people"
            )
            assert topic_people.json()["items"][0]["name"] == "Anna"
            topic_history = await client.get(
                f"/api/v1/topics/{topic_id}/content?section=history"
            )
            assert len(topic_history.json()["items"]) == 3
            people = await client.get("/api/v1/people")
            assert people.json()["items"][0]["display_name"] == "Anna"
            assert people.json()["items"][0]["open_item_count"] == 2
            assert all(item["id"] != str(one_off_id) for item in people.json()["items"])
            all_people = await client.get("/api/v1/people?scope=all")
            assert {item["display_name"] for item in all_people.json()["items"]} == {
                "Anna",
                "One-off contact",
            }
            first_people_page = await client.get("/api/v1/people?scope=all&limit=1")
            second_people_page = await client.get(
                "/api/v1/people?scope=all&limit=1&offset=1"
            )
            assert first_people_page.json()["has_more"] is True
            assert second_people_page.json()["has_more"] is False
            assert (
                first_people_page.json()["items"][0]["id"]
                != second_people_page.json()["items"][0]["id"]
            )
            assert not (await client.get("/api/v1/people?scope=all&q=One-off")).json()[
                "has_more"
            ]
            assert (
                await client.get("/api/v1/people?scope=archived")
            ).status_code == 422
            person_details = await client.get(f"/api/v1/people/{person_id}")
            assert person_details.json()["role"] == "Owner"
            person_follow_ups = await client.get(
                f"/api/v1/people/{person_id}/content?section=follow_ups"
            )
            assert person_follow_ups.json()["items"][0]["title"] == "Ask Anna"
            person_topics = await client.get(
                f"/api/v1/people/{person_id}/content?section=topics"
            )
            assert person_topics.json()["items"][0]["name"] == "Launch"
            person_history = await client.get(
                f"/api/v1/people/{person_id}/content?section=history"
            )
            assert len(person_history.json()["items"]) == 2

            agenda_response = await client.get("/api/v1/agenda")
            agenda_entry = next(
                entry
                for entry in agenda_response.json()["items"]
                if entry["item"]["id"] == str(agenda_id)
            )
            assert agenda_entry["group_kind"] == "person"
            assert agenda_entry["group_label"] == "Anna"
            filtered_agenda = await client.get(
                f"/api/v1/agenda?group_kind=person&group_id={person_id}"
            )
            assert [
                entry["item"]["id"] for entry in filtered_agenda.json()["items"]
            ] == [str(agenda_id)]
            assert not (
                await client.get("/api/v1/agenda?group_kind=unassigned")
            ).json()["items"]

            rejected = await client.post(
                f"/api/v1/work-items/{overdue_id}/actions",
                headers={"Origin": ORIGIN},
                json={
                    "action": "complete",
                    "client_action_id": "f2e9acbc-4f80-4536-96e8-146c28a3ea28",
                    "expected_revision": today.json()["items"][0]["revision"],
                },
            )
            assert rejected.status_code == 403
            completed = await client.post(
                f"/api/v1/work-items/{overdue_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "complete",
                    "client_action_id": "f2e9acbc-4f80-4536-96e8-146c28a3ea28",
                    "expected_revision": today.json()["items"][0]["revision"],
                },
            )
            assert completed.status_code == 200, completed.text
            assert completed.json()["work_item"]["status"] == "done"
            duplicate = await client.post(
                f"/api/v1/work-items/{overdue_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "complete",
                    "client_action_id": "f2e9acbc-4f80-4536-96e8-146c28a3ea28",
                    "expected_revision": today.json()["items"][0]["revision"],
                },
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["changed"] is False

            assert (
                await client.get(f"/api/v1/people/{foreign_person_id}")
            ).status_code == 404
            assert (
                await client.get(f"/api/v1/topics/{foreign_topic_id}")
            ).status_code == 404
            hidden_action = await client.post(
                f"/api/v1/work-items/{foreign_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "complete",
                    "client_action_id": "e5b02f4f-1196-4a63-b7e8-cad6f38cb4bf",
                    "expected_revision": 0,
                },
            )
            assert hidden_action.status_code == 404


@pytest.mark.integration
async def test_pwa_work_item_action_variants(database_engine: AsyncEngine) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
    )
    now = datetime.now(UTC)
    async with started_app(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await authenticated_client(client, sender)
            async with AsyncSession(database_engine) as session:
                user = await session.scalar(
                    select(User).where(User.telegram_user_id == TELEGRAM_USER_ID)
                )
                assert user is not None
                topic = await create_topic(session, user.id, "Action test")
                task = await create_work_item(
                    session,
                    user.id,
                    item_type="task",
                    title="Action task",
                    topic_id=topic.id,
                    due_at=now + timedelta(days=1),
                )
                waiting = await create_work_item(
                    session,
                    user.id,
                    item_type="waiting",
                    title="Waiting item",
                    topic_id=topic.id,
                    due_at=now,
                    waiting_since=now - timedelta(days=1),
                )
                agenda = await create_work_item(
                    session,
                    user.id,
                    item_type="agenda_item",
                    title="Agenda item",
                    topic_id=topic.id,
                )
                discussed = await create_work_item(
                    session,
                    user.id,
                    item_type="agenda_item",
                    title="Discussed item",
                    topic_id=topic.id,
                )
                question = await create_work_item(
                    session,
                    user.id,
                    item_type="question",
                    title="Question item",
                    topic_id=topic.id,
                )
                ids = {
                    "task": task.id,
                    "waiting": waiting.id,
                    "agenda": agenda.id,
                    "discussed": discussed.id,
                    "question": question.id,
                }
                topic_id = topic.id
                user_id = user.id
                await session.commit()

            active = await client.get(
                f"/api/v1/topics/{topic_id}/content?section=active&limit=20"
            )
            assert active.status_code == 200
            cards = {item["id"]: item for item in active.json()["items"]}

            async def run_action(
                item_id: object,
                action: str,
                revision: int,
                **extra: object,
            ) -> dict[str, Any]:
                response = await client.post(
                    f"/api/v1/work-items/{item_id}/actions",
                    headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                    json={
                        "action": action,
                        "client_action_id": str(uuid4()),
                        "expected_revision": revision,
                        **extra,
                    },
                )
                assert response.status_code == 200, response.text
                return cast(dict[str, Any], response.json())

            task_card = cards[str(ids["task"])]
            task_result = await run_action(
                ids["task"], "add_note", task_card["revision"], content="Context"
            )
            task_result = await run_action(
                ids["task"],
                "reschedule",
                task_result["work_item"]["revision"],
                local_date=(now + timedelta(days=2)).date().isoformat(),
                local_time="09:30:00",
            )
            stale = await client.post(
                f"/api/v1/work-items/{ids['task']}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "complete",
                    "client_action_id": str(uuid4()),
                    "expected_revision": task_card["revision"],
                },
            )
            assert stale.status_code == 409
            reminder = task_result["work_item"]["reminder"]
            snoozed = await run_action(
                ids["task"],
                "snooze",
                task_result["work_item"]["revision"],
                duration_minutes=30,
                reminder_id=reminder["id"],
                reminder_revision=reminder["revision"],
            )
            assert snoozed["changed"] is True
            task_result = await run_action(
                ids["task"], "complete", task_result["work_item"]["revision"]
            )
            assert task_result["work_item"]["status"] == "done"
            task_result = await run_action(
                ids["task"], "reopen", task_result["work_item"]["revision"]
            )
            task_result = await run_action(
                ids["task"], "cancel", task_result["work_item"]["revision"]
            )
            assert task_result["work_item"]["status"] == "cancelled"

            waiting_result = await run_action(
                ids["waiting"],
                "waiting_received",
                cards[str(ids["waiting"])]["revision"],
            )
            assert waiting_result["work_item"]["status"] == "done"

            agenda_result = await run_action(
                ids["agenda"],
                "add_result",
                cards[str(ids["agenda"])]["revision"],
                content="Agreed next step",
            )
            agenda_result = await run_action(
                ids["agenda"],
                "add_decision",
                agenda_result["work_item"]["revision"],
                content="Ship on Friday",
            )
            assert agenda_result["decision_id"]
            decision_id = agenda_result["decision_id"]
            agenda_result = await run_action(
                ids["agenda"],
                "defer",
                agenda_result["work_item"]["revision"],
                local_date=(now + timedelta(days=3)).date().isoformat(),
                local_time="11:00:00",
            )
            agenda_result = await run_action(
                ids["agenda"],
                "convert_to_task",
                agenda_result["work_item"]["revision"],
            )
            assert agenda_result["work_item"]["type"] == "task"

            discussed_result = await run_action(
                ids["discussed"],
                "agenda_discussed",
                cards[str(ids["discussed"])]["revision"],
            )
            answered_result = await run_action(
                ids["question"],
                "question_answered",
                cards[str(ids["question"])]["revision"],
            )
            assert discussed_result["work_item"]["status"] == "done"
            assert answered_result["work_item"]["status"] == "done"

            async with AsyncSession(database_engine) as session:
                decision = await session.get(WorkItem, decision_id)
                assert decision is not None
                assert decision.type == "decision" and decision.status == "done"
                relation = await session.scalar(
                    select(WorkItemRelation).where(
                        WorkItemRelation.source_work_item_id == decision.id,
                        WorkItemRelation.target_work_item_id == ids["agenda"],
                    )
                )
                assert relation is not None
                note_count = len(
                    list(
                        await session.scalars(
                            select(NoteLink).where(
                                NoteLink.user_id == user_id,
                                NoteLink.work_item_id.in_((ids["task"], ids["agenda"])),
                            )
                        )
                    )
                )
                assert note_count == 2
                event_rows = await session.execute(
                    select(WorkItemEvent.work_item_id, WorkItemEvent.event_type).where(
                        WorkItemEvent.work_item_id.in_(
                            (ids["agenda"], ids["discussed"], ids["question"])
                        )
                    )
                )
                events_by_item: dict[object, set[str]] = {}
                for event_item_id, event_type in event_rows:
                    events_by_item.setdefault(event_item_id, set()).add(event_type)
                assert {"note_added", "updated", "rescheduled"}.issubset(
                    events_by_item[ids["agenda"]]
                )
                assert "completed" in events_by_item[ids["discussed"]]
                assert "completed" in events_by_item[ids["question"]]
                task_reminders = list(
                    await session.scalars(
                        select(Reminder).where(Reminder.work_item_id == ids["task"])
                    )
                )
                assert task_reminders
                assert all(
                    item.status == ReminderStatus.CANCELLED.value
                    for item in task_reminders
                )


@pytest.mark.integration
async def test_today_grouping_respects_local_day_and_semantic_types(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, TELEGRAM_USER_ID + 123)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    local_midnight = datetime(2026, 7, 22, 4, tzinfo=UTC)
    items: dict[TodaySection, WorkItem] = {
        "overdue": await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title="Previous local day",
            due_at=local_midnight - timedelta(microseconds=1),
        ),
        "due_today": await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title="Current local day",
            due_at=local_midnight,
        ),
        "follow_ups": await create_work_item(
            database_session,
            user.id,
            item_type="follow_up",
            title="Semantic follow-up",
            next_follow_up_at=local_midnight - timedelta(days=2),
        ),
        "waiting": await create_work_item(
            database_session,
            user.id,
            item_type="waiting",
            title="Semantic waiting",
            due_at=local_midnight - timedelta(days=2),
            waiting_since=local_midnight - timedelta(days=3),
        ),
        "questions": await create_work_item(
            database_session,
            user.id,
            item_type="question",
            title="Semantic question",
            due_at=local_midnight - timedelta(days=2),
        ),
    }
    preferences = effective_preferences(
        None,
        NotificationDefaults(
            timezone="America/New_York",
            morning_digest_time=time(8),
            evening_digest_time=time(18),
            quiet_hours_start=time(22),
            quiet_hours_end=time(7),
            snooze_minutes=60,
        ),
    )

    for section, expected in items.items():
        page = await list_today_section(
            database_session,
            user.id,
            section,
            now=now,
            preferences=preferences,
            limit=20,
            offset=0,
        )
        assert [card.id for card in page.items] == [expected.id]
