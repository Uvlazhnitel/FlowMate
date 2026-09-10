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
    Note,
    NoteLink,
    Reminder,
    User,
    WorkItem,
    WorkItemActionSession,
    WorkItemEvent,
    WorkItemRelation,
)
from flowmate.db.users import create_telegram_user
from flowmate.reminders.enums import ReminderStatus
from flowmate.reminders.preferences import NotificationDefaults, effective_preferences
from flowmate.task_engine.operational import (
    TodaySection,
    dashboard_snapshot,
    list_today_section,
    list_tomorrow_items,
    today_overview_snapshot,
)
from flowmate.task_engine.rescheduling import UNKNOWN_PHRASE_MESSAGE
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
)
from flowmate.workspaces import workspace_context
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
async def test_work_item_workspace_action_moves_scoped_dependencies(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
    )
    scheduled_at = datetime.now(UTC) + timedelta(hours=2)
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
                with workspace_context(session, user_id=user.id, workspace="personal"):
                    personal_topic = await create_topic(session, user.id, "Launch")
                    source_note = Note(
                        user_id=user.id,
                        content="Private source context",
                        source="manual",
                    )
                    session.add(source_note)
                    await session.flush()
                    item = await create_work_item(
                        session,
                        user.id,
                        item_type="task",
                        title="Move between workspaces",
                        topic_id=personal_topic.id,
                        due_at=scheduled_at,
                        source_note_id=source_note.id,
                    )
                    item.status = "active"
                    item.planner_status = "transferred"
                    link = NoteLink(
                        user_id=user.id,
                        note_id=source_note.id,
                        work_item_id=item.id,
                    )
                    pending = Reminder(
                        user_id=user.id,
                        work_item_id=item.id,
                        type="custom",
                        scheduled_at=scheduled_at,
                        deduplication_key=f"workspace-pending:{item.id}",
                    )
                    sent = Reminder(
                        user_id=user.id,
                        work_item_id=item.id,
                        type="custom",
                        status="sent",
                        scheduled_at=scheduled_at,
                        sent_at=scheduled_at,
                        deduplication_key=f"workspace-sent:{item.id}",
                    )
                    session.add_all([link, pending, sent])
                with workspace_context(session, user_id=user.id, workspace="work"):
                    work_topic = await create_topic(session, user.id, " launch ")
                item_id = item.id
                source_note_id = source_note.id
                link_id = link.id
                personal_topic_id = personal_topic.id
                work_topic_id = work_topic.id
                original_status = item.status
                original_due_at = item.due_at
                original_planner_status = item.planner_status
                await session.commit()

            overview = await client.get("/api/v1/overview?workspace=personal")
            entry = next(
                value
                for value in overview.json()["today"]["items"]
                if value["item"]["id"] == str(item_id)
            )
            action_id = str(uuid4())
            payload = {
                "action": "move_workspace",
                "target": "work",
                "client_action_id": action_id,
                "expected_revision": entry["item"]["revision"],
            }
            moved = await client.post(
                f"/api/v1/work-items/{item_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json=payload,
            )
            assert moved.status_code == 200, moved.text
            moved_item = moved.json()["work_item"]
            assert moved_item["workspace"] == "work"
            assert moved_item["topic_id"] == str(work_topic_id)
            assert moved_item["status"] == original_status
            assert datetime.fromisoformat(moved_item["due_at"]) == original_due_at
            assert moved_item["planner_status"] == original_planner_status
            assert (await client.get("/api/v1/auth/me")).json()[
                "active_workspace"
            ] == "personal"

            personal_view = await client.get("/api/v1/overview?workspace=personal")
            work_view = await client.get("/api/v1/overview?workspace=work")
            assert all(
                value["item"]["id"] != str(item_id)
                for value in personal_view.json()["today"]["items"]
            )
            assert any(
                value["item"]["id"] == str(item_id)
                for value in work_view.json()["today"]["items"]
            )

            duplicate = await client.post(
                f"/api/v1/work-items/{item_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json=payload,
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["changed"] is False
            assert duplicate.json()["work_item"]["workspace"] == "work"

            async with AsyncSession(database_engine) as session:
                stored_item = await session.scalar(
                    select(WorkItem)
                    .where(WorkItem.id == item_id)
                    .execution_options(include_all_workspaces=True)
                )
                assert stored_item is not None
                reminders = list(
                    await session.scalars(
                        select(Reminder)
                        .where(Reminder.work_item_id == item_id)
                        .execution_options(include_all_workspaces=True)
                    )
                )
                note = await session.scalar(
                    select(Note)
                    .where(Note.id == source_note_id)
                    .execution_options(include_all_workspaces=True)
                )
                stored_link = await session.get(NoteLink, link_id)
                events = list(
                    await session.scalars(
                        select(WorkItemEvent).where(
                            WorkItemEvent.work_item_id == item_id,
                            WorkItemEvent.event_type == "workspace_changed",
                        )
                    )
                )
                assert stored_item.workspace == "work"
                assert {reminder.workspace for reminder in reminders} == {"work"}
                assert {reminder.status for reminder in reminders} == {
                    "pending",
                    "sent",
                }
                assert note is not None and note.workspace == "personal"
                assert stored_link is not None and stored_link.note_id == source_note_id
                assert len(events) == 1
                assert events[0].payload == {"previous": "personal", "new": "work"}

            returned = await client.post(
                f"/api/v1/work-items/{item_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "move_workspace",
                    "target": "personal",
                    "client_action_id": str(uuid4()),
                    "expected_revision": moved_item["revision"],
                },
            )
            assert returned.status_code == 200, returned.text
            assert returned.json()["work_item"]["workspace"] == "personal"
            assert returned.json()["work_item"]["topic_id"] == str(personal_topic_id)


@pytest.mark.integration
async def test_work_item_workspace_action_rejects_conflicts_and_foreign_items(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
    )
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
                item = await create_work_item(
                    session, user.id, item_type="question", title="Active Telegram edit"
                )
                action_session = WorkItemActionSession(
                    user_id=user.id,
                    work_item_id=item.id,
                    action="add_note",
                    expires_at=datetime.now(UTC) + timedelta(minutes=10),
                )
                session.add(action_session)
                foreign_user = await create_telegram_user(
                    session, TELEGRAM_USER_ID + 900
                )
                foreign_item = await create_work_item(
                    session,
                    foreign_user.id,
                    item_type="task",
                    title="Foreign workspace item",
                )
                item_id = item.id
                action_session_id = action_session.id
                foreign_id = foreign_item.id
                await session.commit()

            overview = await client.get("/api/v1/overview?workspace=personal")
            entry = next(
                value
                for value in overview.json()["inbox"]["items"]
                if value["id"] == str(item_id)
            )
            revision = entry["item"]["revision"]
            conflict = await client.post(
                f"/api/v1/work-items/{item_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "move_workspace",
                    "target": "work",
                    "client_action_id": str(uuid4()),
                    "expected_revision": revision,
                },
            )
            assert conflict.status_code == 409
            assert "Телеграм" in conflict.text

            async with AsyncSession(database_engine) as session:
                stored_session = await session.get(
                    WorkItemActionSession, action_session_id
                )
                assert stored_session is not None
                stored_session.status = "cancelled"
                await session.commit()

            stale = await client.post(
                f"/api/v1/work-items/{item_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "move_workspace",
                    "target": "work",
                    "client_action_id": str(uuid4()),
                    "expected_revision": revision - 1,
                },
            )
            assert stale.status_code == 409
            unchanged = await client.post(
                f"/api/v1/work-items/{item_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "move_workspace",
                    "target": "personal",
                    "client_action_id": str(uuid4()),
                    "expected_revision": revision,
                },
            )
            assert unchanged.status_code == 409
            foreign = await client.post(
                f"/api/v1/work-items/{foreign_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "move_workspace",
                    "target": "personal",
                    "client_action_id": str(uuid4()),
                    "expected_revision": 0,
                },
            )
            assert foreign.status_code == 404


@pytest.mark.integration
async def test_overview_bucket_action_moves_task_and_is_idempotent(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
    )
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
                item = await create_work_item(
                    session,
                    user.id,
                    item_type="task",
                    title="Move from inbox",
                )
                foreign = await create_telegram_user(session, TELEGRAM_USER_ID + 900)
                foreign_item = await create_work_item(
                    session,
                    foreign.id,
                    item_type="task",
                    title="Private task",
                )
                item_id = item.id
                foreign_id = foreign_item.id
                await session.commit()

            overview = await client.get("/api/v1/overview")
            assert overview.status_code == 200
            inbox_entry = next(
                entry
                for entry in overview.json()["inbox"]["items"]
                if entry["kind"] == "work_item" and entry["id"] == str(item_id)
            )
            assert inbox_entry["item"]["id"] == str(item_id)
            assert isinstance(inbox_entry["item"]["revision"], int)
            action_id = str(uuid4())
            payload = {
                "action": "move_bucket",
                "target": "tomorrow",
                "client_action_id": action_id,
                "expected_revision": inbox_entry["item"]["revision"],
            }
            moved = await client.post(
                f"/api/v1/work-items/{item_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json=payload,
            )
            assert moved.status_code == 200, moved.text
            moved_item = moved.json()["work_item"]
            assert moved_item["status"] == "planned"
            assert moved_item["inbox_triaged_at"] is not None
            assert datetime.fromisoformat(moved_item["effective_at"]).time() == time(9)

            duplicate = await client.post(
                f"/api/v1/work-items/{item_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json=payload,
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["changed"] is False

            refreshed = await client.get("/api/v1/overview")
            assert all(
                entry["id"] != str(item_id)
                for entry in refreshed.json()["inbox"]["items"]
            )
            assert any(
                entry["item"]["id"] == str(item_id)
                for entry in refreshed.json()["tomorrow"]["items"]
            )

            stale = await client.post(
                f"/api/v1/work-items/{item_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "move_bucket",
                    "target": "today",
                    "client_action_id": str(uuid4()),
                    "expected_revision": inbox_entry["item"]["revision"],
                },
            )
            assert stale.status_code == 409
            private = await client.post(
                f"/api/v1/work-items/{foreign_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "move_bucket",
                    "target": "tomorrow",
                    "client_action_id": str(uuid4()),
                    "expected_revision": 0,
                },
            )
            assert private.status_code == 404

            returned = await client.post(
                f"/api/v1/work-items/{item_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "move_bucket",
                    "target": "inbox",
                    "client_action_id": str(uuid4()),
                    "expected_revision": moved_item["revision"],
                },
            )
            assert returned.status_code == 200, returned.text
            assert returned.json()["work_item"]["effective_at"] is None
            assert returned.json()["work_item"]["status"] == "inbox"


@pytest.mark.integration
async def test_pwa_operational_workspace_scope_is_independent_from_creation_scope(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
    )
    due_at = datetime.now(UTC) + timedelta(hours=1)
    tomorrow_at = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
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
                personal_today = WorkItem(
                    user_id=user.id,
                    workspace="personal",
                    type="task",
                    title="Personal only",
                    status="active",
                    priority="normal",
                    planner_status="needs_transfer",
                    due_at=due_at,
                )
                session.add_all(
                    [
                        personal_today,
                        WorkItem(
                            user_id=user.id,
                            workspace="personal",
                            type="follow_up",
                            title="Personal tomorrow",
                            status="active",
                            priority="normal",
                            planner_status="not_required",
                            next_follow_up_at=tomorrow_at,
                        ),
                        WorkItem(
                            user_id=user.id,
                            workspace="work",
                            type="waiting",
                            title="Work tomorrow",
                            status="waiting",
                            priority="normal",
                            planner_status="not_required",
                            due_at=tomorrow_at,
                            waiting_since=due_at,
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
                await session.flush()
                personal_today_id = personal_today.id
                await session.commit()

            combined = await client.get("/api/v1/today?section=due_today")
            assert {item["title"] for item in combined.json()["items"]} == {
                "Personal only",
                "Work only",
            }
            assert combined.json()["total"] == 2
            assert combined.json()["workspace_counts"] == {
                "all": 2,
                "work": 1,
                "personal": 1,
            }
            assert {item["workspace"] for item in combined.json()["items"]} == {
                "work",
                "personal",
            }
            combined_overview = await client.get("/api/v1/today/overview")
            assert {item["title"] for item in combined_overview.json()["focus"]} == {
                "Personal only",
                "Work only",
            }
            combined_home = await client.get("/api/v1/overview")
            assert combined_home.status_code == 200
            assert {
                entry["item"]["title"]
                for entry in combined_home.json()["today"]["items"]
            } == {"Personal only", "Work only"}
            combined_tomorrow = await client.get("/api/v1/tomorrow")
            assert {item["title"] for item in combined_tomorrow.json()["items"]} == {
                "Personal tomorrow",
                "Work tomorrow",
            }

            personal = await client.get(
                "/api/v1/today?section=due_today&workspace=personal"
            )
            assert [item["title"] for item in personal.json()["items"]] == [
                "Personal only"
            ]
            work = await client.get("/api/v1/today?section=due_today&workspace=work")
            assert [item["title"] for item in work.json()["items"]] == ["Work only"]
            unknown = await client.get(
                "/api/v1/today?section=due_today&workspace=unexpected"
            )
            assert {item["title"] for item in unknown.json()["items"]} == {
                "Personal only",
                "Work only",
            }

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
            still_combined = await client.get("/api/v1/today?section=due_today")
            assert {item["title"] for item in still_combined.json()["items"]} == {
                "Personal only",
                "Work only",
            }

            personal_item = personal.json()["items"][0]
            moved = await client.post(
                f"/api/v1/work-items/{personal_today_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "move_bucket",
                    "target": "tomorrow",
                    "client_action_id": str(uuid4()),
                    "expected_revision": personal_item["revision"],
                },
            )
            assert moved.status_code == 200, moved.text
            assert moved.json()["work_item"]["workspace"] == "personal"


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
            assert (await client.get("/api/v1/overview")).status_code == 401
            assert (await client.get("/api/v1/today/overview")).status_code == 401
            assert (await client.get("/api/v1/tomorrow")).status_code == 401
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
                "planner_queue": 0,
            }
            assert dashboard.json()["recommended"][0]["title"] == "Prepare launch"
            assert len(dashboard.json()["activity"]) == 3
            overview = await client.get("/api/v1/today/overview")
            assert overview.status_code == 200
            assert overview.json()["timezone"] == "UTC"
            assert overview.json()["summary"] == dashboard.json()["summary"]
            assert overview.json()["focus"][0]["title"] == "Prepare launch"
            assert all(
                item["id"] != str(foreign_id) for item in overview.json()["focus"]
            )
            assert (
                app.openapi()["paths"]["/api/v1/dashboard"]["get"]["deprecated"] is True
            )
            assert "deprecated" not in app.openapi()["paths"]["/api/v1/overview"]["get"]

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
            preset_action_id = str(uuid4())
            preset_payload = {
                "action": "reschedule_preset",
                "client_action_id": preset_action_id,
                "expected_revision": task_result["work_item"]["revision"],
                "preset": "tomorrow_morning",
            }
            preset = await client.post(
                f"/api/v1/work-items/{ids['task']}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json=preset_payload,
            )
            assert preset.status_code == 200, preset.text
            task_result = preset.json()
            assert datetime.fromisoformat(
                task_result["work_item"]["effective_at"]
            ).time() == time(9)
            duplicate_preset = await client.post(
                f"/api/v1/work-items/{ids['task']}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json=preset_payload,
            )
            assert duplicate_preset.status_code == 200
            assert duplicate_preset.json()["changed"] is False

            task_result = await run_action(
                ids["task"],
                "reschedule_text",
                task_result["work_item"]["revision"],
                phrase="в пятницу после обеда",
            )
            assert datetime.fromisoformat(
                task_result["work_item"]["effective_at"]
            ).time() == time(15)
            assert (
                task_result["work_item"]["reminder"]["effective_at"]
                == (task_result["work_item"]["effective_at"])
            )
            due_before_unknown = task_result["work_item"]["effective_at"]
            unknown = await client.post(
                f"/api/v1/work-items/{ids['task']}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "reschedule_text",
                    "client_action_id": str(uuid4()),
                    "expected_revision": task_result["work_item"]["revision"],
                    "phrase": "когда получится",
                },
            )
            assert unknown.status_code == 422
            assert unknown.json()["error"]["message"] == UNKNOWN_PHRASE_MESSAGE
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
                persisted_task = await session.get(WorkItem, ids["task"])
                assert persisted_task is not None
                assert persisted_task.due_at is not None
                assert persisted_task.due_at == datetime.fromisoformat(
                    due_before_unknown
                )
                task_event_types = list(
                    await session.scalars(
                        select(WorkItemEvent.event_type).where(
                            WorkItemEvent.work_item_id == ids["task"]
                        )
                    )
                )
                assert "rescheduled" in task_event_types
                assert task_event_types.count("rescheduled") == 3
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
            title="Later today",
            due_at=now + timedelta(hours=1),
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

    elapsed_today = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Elapsed earlier today",
        due_at=now - timedelta(hours=1),
    )
    overdue_page = await list_today_section(
        database_session,
        user.id,
        "overdue",
        now=now,
        preferences=preferences,
        limit=20,
        offset=0,
    )
    assert [card.id for card in overdue_page.items] == [
        items["overdue"].id,
        elapsed_today.id,
    ]
    due_today_page = await list_today_section(
        database_session,
        user.id,
        "due_today",
        now=now,
        preferences=preferences,
        limit=20,
        offset=0,
    )
    assert [card.id for card in due_today_page.items] == [items["due_today"].id]

    dashboard = await dashboard_snapshot(
        database_session,
        user.id,
        now=now,
        preferences=preferences,
    )
    summary = cast(dict[str, int], dashboard["summary"])
    assert summary["overdue"] == 2
    assert summary["due_today"] == 1
    overview = await today_overview_snapshot(
        database_session,
        user.id,
        now=now,
        preferences=preferences,
    )
    overview_summary = cast(dict[str, int], overview["summary"])
    assert overview_summary["overdue"] == 2
    assert overview_summary["due_today"] == 1


@pytest.mark.integration
async def test_tomorrow_list_uses_local_dst_bounds_and_effective_dates(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, TELEGRAM_USER_ID + 125)
    now = datetime(2026, 3, 7, 17, tzinfo=UTC)
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
    task = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Early task",
        status="active",
        due_at=datetime(2026, 3, 8, 6, tzinfo=UTC),
    )
    follow_up = await create_work_item(
        database_session,
        user.id,
        item_type="follow_up",
        title="Follow up",
        status="active",
        next_follow_up_at=datetime(2026, 3, 8, 12, tzinfo=UTC),
    )
    urgent_task = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Urgent at the same time",
        status="active",
        priority="urgent",
        due_at=datetime(2026, 3, 8, 12, tzinfo=UTC),
    )
    waiting = await create_work_item(
        database_session,
        user.id,
        item_type="waiting",
        title="Waiting",
        status="waiting",
        due_at=datetime(2026, 3, 8, 18, tzinfo=UTC),
        waiting_since=datetime(2026, 3, 7, 18, tzinfo=UTC),
    )
    question = await create_work_item(
        database_session,
        user.id,
        item_type="question",
        title="Question",
        status="active",
        due_at=datetime(2026, 3, 9, 3, 59, tzinfo=UTC),
    )
    for title, due_at, status in (
        ("Before tomorrow", datetime(2026, 3, 8, 4, 59, tzinfo=UTC), "active"),
        ("After tomorrow", datetime(2026, 3, 9, 4, tzinfo=UTC), "active"),
        ("Completed tomorrow", datetime(2026, 3, 8, 10, tzinfo=UTC), "done"),
    ):
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title=title,
            status=status,
            due_at=due_at,
            completed_at=due_at if status == "done" else None,
        )
    await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="No date",
        status="active",
    )
    await create_work_item(
        database_session,
        user.id,
        item_type="agenda_item",
        title="Dated agenda item",
        status="active",
        due_at=datetime(2026, 3, 8, 14, tzinfo=UTC),
    )

    page = await list_tomorrow_items(
        database_session,
        user.id,
        now=now,
        preferences=preferences,
        limit=20,
        offset=0,
    )

    assert [card.id for card in page.items] == [
        task.id,
        urgent_task.id,
        follow_up.id,
        waiting.id,
        question.id,
    ]
    assert page.has_more is False


@pytest.mark.integration
async def test_today_overview_focus_preserves_category_and_priority_order(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, TELEGRAM_USER_ID + 124)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    preferences = effective_preferences(
        None,
        NotificationDefaults(
            timezone="UTC",
            morning_digest_time=time(8),
            evening_digest_time=time(18),
            quiet_hours_start=time(22),
            quiet_hours_end=time(7),
            snooze_minutes=60,
        ),
    )
    urgent = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Urgent overdue",
        status="active",
        priority="urgent",
        due_at=now - timedelta(hours=1),
    )
    high = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="High overdue",
        status="active",
        priority="high",
        due_at=now - timedelta(hours=2),
    )
    follow_up = await create_work_item(
        database_session,
        user.id,
        item_type="follow_up",
        title="Due follow-up",
        status="active",
        next_follow_up_at=now,
    )
    waiting = await create_work_item(
        database_session,
        user.id,
        item_type="waiting",
        title="Overdue waiting",
        status="waiting",
        due_at=now - timedelta(minutes=30),
    )
    due_today = await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Due today",
        status="active",
        due_at=now + timedelta(hours=1),
    )
    question = await create_work_item(
        database_session,
        user.id,
        item_type="question",
        title="Question",
        status="active",
        due_at=now - timedelta(days=2),
    )

    overview = await today_overview_snapshot(
        database_session,
        user.id,
        now=now,
        preferences=preferences,
    )
    focus = cast(list[Any], overview["focus"])
    assert [item.id for item in focus] == [
        urgent.id,
        high.id,
        follow_up.id,
        waiting.id,
        due_today.id,
    ]
    assert cast(dict[str, int], overview["summary"])["overdue"] == 2

    high.status = "done"
    await database_session.flush()
    refreshed = await today_overview_snapshot(
        database_session,
        user.id,
        now=now,
        preferences=preferences,
    )
    refreshed_focus = cast(list[Any], refreshed["focus"])
    assert [item.id for item in refreshed_focus] == [
        urgent.id,
        follow_up.id,
        waiting.id,
        due_today.id,
        question.id,
    ]


@pytest.mark.integration
async def test_today_overview_later_today_is_bounded_sorted_and_disjoint(
    database_session: AsyncSession,
) -> None:
    user = await create_telegram_user(database_session, TELEGRAM_USER_ID + 125)
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    preferences = effective_preferences(
        None,
        NotificationDefaults(
            timezone="UTC",
            morning_digest_time=time(8),
            evening_digest_time=time(18),
            quiet_hours_start=time(22),
            quiet_hours_end=time(7),
            snooze_minutes=60,
        ),
    )
    for index in range(3):
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title=f"Overdue {index}",
            status="active",
            due_at=now - timedelta(hours=index + 1),
        )
    await create_work_item(
        database_session,
        user.id,
        item_type="follow_up",
        title="Focus follow-up",
        status="active",
        next_follow_up_at=now,
    )
    await create_work_item(
        database_session,
        user.id,
        item_type="waiting",
        title="Focus waiting",
        status="waiting",
        due_at=now - timedelta(minutes=10),
    )
    later_items: list[WorkItem] = []
    shared_due_at = now + timedelta(hours=1)
    later_items.append(
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title="Later low",
            status="active",
            priority="low",
            due_at=shared_due_at,
        )
    )
    later_items.append(
        await create_work_item(
            database_session,
            user.id,
            item_type="task",
            title="Later urgent",
            status="active",
            priority="urgent",
            due_at=shared_due_at,
        )
    )
    for index in range(10):
        later_items.append(
            await create_work_item(
                database_session,
                user.id,
                item_type="task",
                title=f"Later {index}",
                status="active",
                due_at=now + timedelta(hours=2, minutes=index),
            )
        )
    await create_work_item(
        database_session,
        user.id,
        item_type="task",
        title="Tomorrow",
        status="active",
        due_at=now + timedelta(days=1),
    )
    for item_type in ("follow_up", "waiting", "question"):
        await create_work_item(
            database_session,
            user.id,
            item_type=item_type,
            title=f"Semantic {item_type}",
            status="waiting" if item_type == "waiting" else "active",
            due_at=now + timedelta(hours=3),
            next_follow_up_at=(
                now + timedelta(hours=3) if item_type == "follow_up" else None
            ),
        )

    overview = await today_overview_snapshot(
        database_session,
        user.id,
        now=now,
        preferences=preferences,
    )
    focus_ids = {item.id for item in cast(list[Any], overview["focus"])}
    later = cast(dict[str, object], overview["later_today"])
    later_cards = cast(list[Any], later["items"])
    later_ids = [item.id for item in later_cards]

    assert len(later_ids) == 10
    assert later["has_more"] is True
    assert focus_ids.isdisjoint(later_ids)
    assert [item.title for item in later_cards[:2]] == [
        "Later urgent",
        "Later low",
    ]
    assert all(item.id in {value.id for value in later_items} for item in later_cards)
    assert cast(dict[str, int], overview["summary"])["due_today"] == 12


@pytest.mark.integration
async def test_pwa_subtasks_are_nested_and_follow_parent_lifecycle(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
    )
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
                topic = await create_topic(session, user.id, "Automation")
                parent = await create_work_item(
                    session,
                    user.id,
                    item_type="agenda_item",
                    title="Build automation",
                    status="active",
                    topic_id=topic.id,
                )
                parent_id = parent.id
                topic_id = topic.id
                user_id = user.id
                await session.commit()

            parent_page = await client.get(
                f"/api/v1/topics/{topic_id}/content?section=active&limit=20"
            )
            assert parent_page.status_code == 200
            parent_card = parent_page.json()["items"][0]
            action_uuid = uuid4()
            action_id = str(action_uuid)
            payload = {
                "title": "Send automation to admins",
                "client_action_id": action_id,
                "expected_revision": parent_card["revision"],
            }
            created = await client.post(
                f"/api/v1/work-items/{parent_id}/subtasks",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json=payload,
            )
            assert created.status_code == 200, created.text
            created_payload = created.json()
            assert created_payload["changed"] is True
            assert created_payload["subtask"]["title"] == "Send automation to admins"
            assert created_payload["subtask"]["status"] == "active"
            assert (
                created_payload["subtask"]["workspace"]
                == created_payload["work_item"]["workspace"]
            )
            assert len(created_payload["work_item"]["subtasks"]) == 1
            subtask_id = created_payload["subtask"]["id"]

            duplicate = await client.post(
                f"/api/v1/work-items/{parent_id}/subtasks",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json=payload,
            )
            assert duplicate.status_code == 200
            assert duplicate.json()["changed"] is False
            assert duplicate.json()["subtask"]["id"] == subtask_id

            nested = await client.post(
                f"/api/v1/work-items/{subtask_id}/subtasks",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "title": "Nested",
                    "client_action_id": str(uuid4()),
                    "expected_revision": created_payload["subtask"]["revision"],
                },
            )
            assert nested.status_code == 409

            child_completed = await client.post(
                f"/api/v1/work-items/{subtask_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "complete",
                    "client_action_id": str(uuid4()),
                    "expected_revision": created_payload["subtask"]["revision"],
                },
            )
            assert child_completed.status_code == 200, child_completed.text
            assert child_completed.json()["work_item"]["status"] == "done"
            child_reopened = await client.post(
                f"/api/v1/work-items/{subtask_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "reopen",
                    "client_action_id": str(uuid4()),
                    "expected_revision": child_completed.json()["work_item"][
                        "revision"
                    ],
                },
            )
            assert child_reopened.status_code == 200, child_reopened.text
            assert child_reopened.json()["work_item"]["status"] == "active"

            refreshed_page = await client.get(
                f"/api/v1/topics/{topic_id}/content?section=active&limit=20"
            )
            refreshed_items = refreshed_page.json()["items"]
            assert [item["id"] for item in refreshed_items] == [str(parent_id)]
            assert refreshed_items[0]["subtasks"][0]["id"] == subtask_id

            completed = await client.post(
                f"/api/v1/work-items/{parent_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "complete",
                    "client_action_id": str(uuid4()),
                    "expected_revision": created_payload["work_item"]["revision"],
                },
            )
            assert completed.status_code == 200, completed.text
            completed_payload = completed.json()["work_item"]
            assert completed_payload["status"] == "done"
            assert completed_payload["subtasks"][0]["status"] == "done"

            reopened = await client.post(
                f"/api/v1/work-items/{parent_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "reopen",
                    "client_action_id": str(uuid4()),
                    "expected_revision": completed_payload["revision"],
                },
            )
            assert reopened.status_code == 200, reopened.text
            reopened_payload = reopened.json()["work_item"]
            assert reopened_payload["status"] == "inbox"
            assert reopened_payload["subtasks"][0]["status"] == "active"

            async with AsyncSession(database_engine) as session:
                subtask = await session.get(WorkItem, subtask_id)
                assert subtask is not None
                assert subtask.user_id == user_id
                assert subtask.topic_id == topic_id
                relation = await session.scalar(
                    select(WorkItemRelation).where(
                        WorkItemRelation.source_work_item_id == parent_id,
                        WorkItemRelation.target_work_item_id == subtask.id,
                        WorkItemRelation.relation_type == "subtask",
                    )
                )
                assert relation is not None
                linked_event = await session.scalar(
                    select(WorkItemEvent).where(
                        WorkItemEvent.work_item_id == parent_id,
                        WorkItemEvent.client_action_id == action_uuid,
                    )
                )
                assert linked_event is not None
                assert "Send automation" not in str(linked_event.payload)

            target_workspace = (
                "work" if reopened_payload["workspace"] == "personal" else "personal"
            )
            moved = await client.post(
                f"/api/v1/work-items/{parent_id}/actions",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json={
                    "action": "move_workspace",
                    "target": target_workspace,
                    "client_action_id": str(uuid4()),
                    "expected_revision": reopened_payload["revision"],
                },
            )
            assert moved.status_code == 200, moved.text
            assert moved.json()["work_item"]["workspace"] == target_workspace
            assert (
                moved.json()["work_item"]["subtasks"][0]["workspace"]
                == target_workspace
            )
