from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from flowmate.api.app import create_app
from flowmate.db.drafts import create_parsing_draft, replace_draft_analysis
from flowmate.db.models import Note, User, WorkItem, WorkItemPerson
from flowmate.db.notes import create_note_idempotently
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
    link_person_to_work_item,
)
from tests.ai_factories import make_analysis_result, make_draft_item, make_parse_result
from tests.conftest import started_app
from tests.integration.test_pwa_auth import (
    ORIGIN,
    TELEGRAM_USER_ID,
    CapturingLoginCodeSender,
    auth_settings,
)
from tests.integration.test_pwa_operations import authenticated_client

FOREIGN_TELEGRAM_USER_ID = TELEGRAM_USER_ID + 901


@pytest.fixture(autouse=True)
async def cleanup_remaining_screen_users(
    database_engine: AsyncEngine,
) -> AsyncIterator[None]:
    yield
    async with AsyncSession(database_engine) as session:
        await session.execute(
            delete(User).where(
                User.telegram_user_id.in_((TELEGRAM_USER_ID, FOREIGN_TELEGRAM_USER_ID))
            )
        )
        await session.commit()


def write_headers(csrf: str) -> dict[str, str]:
    return {"Origin": ORIGIN, "X-CSRF-Token": csrf}


@pytest.mark.integration
async def test_inbox_edit_uncertainty_conversion_and_isolation(
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
            assert (await client.get("/api/v1/inbox")).status_code == 401
            csrf = await authenticated_client(client, sender)
            async with AsyncSession(database_engine) as session:
                user = await session.scalar(
                    select(User).where(User.telegram_user_id == TELEGRAM_USER_ID)
                )
                assert user is not None
                topic = await create_topic(session, user.id, "Release")
                person = await create_person(session, user.id, "Nina")
                source, _ = await create_note_idempotently(
                    session,
                    user_id=user.id,
                    content="Maybe prepare the release",
                    source="text",
                    telegram_update_id=790_001,
                )
                draft = await create_parsing_draft(
                    session,
                    user_id=user.id,
                    source_note_id=source.id,
                    ttl_hours=24,
                )
                analysis = make_analysis_result(
                    make_parse_result(
                        [
                            make_draft_item(
                                title="Prepare release",
                                confidence=0.4,
                                missing_fields=["topic", "due_date"],
                            )
                        ]
                    )
                )
                await replace_draft_analysis(
                    session, draft, analysis, question=None, ttl_hours=24
                )
                session.add(
                    Note(user_id=user.id, content="Loose note", source="manual")
                )
                incomplete = await create_work_item(
                    session, user.id, item_type="task", title="Unplanned item"
                )
                foreign = await create_telegram_user(session, FOREIGN_TELEGRAM_USER_ID)
                foreign_note = Note(
                    user_id=foreign.id, content="Private note", source="manual"
                )
                session.add(foreign_note)
                await session.flush()
                draft_id = draft.id
                item_id = draft.items[0].id
                topic_id = topic.id
                person_id = person.id
                foreign_note_id = foreign_note.id
                incomplete_id = incomplete.id
                await session.commit()

            inbox = await client.get("/api/v1/inbox?limit=2")
            assert inbox.status_code == 200
            assert inbox.json()["has_more"] is True
            all_inbox = await client.get("/api/v1/inbox?limit=20")
            entries = all_inbox.json()["items"]
            assert {entry["kind"] for entry in entries} == {
                "draft",
                "note",
                "work_item",
            }
            assert "Private note" not in all_inbox.text
            draft_entry = next(entry for entry in entries if entry["kind"] == "draft")
            assert {"low_confidence", "incomplete"} <= set(draft_entry["reasons"])
            work_entry = next(
                entry
                for entry in entries
                if entry["kind"] == "work_item"
                and entry["item"]["id"] == str(incomplete_id)
            )
            assert {"inbox_status", "missing_date", "missing_topic"} <= set(
                work_entry["reasons"]
            )
            scheduled = await client.post(
                f"/api/v1/work-items/{incomplete_id}/actions",
                headers=write_headers(csrf),
                json={
                    "action": "edit",
                    "client_action_id": str(uuid4()),
                    "expected_revision": work_entry["item"]["revision"],
                    "title": "Unplanned item",
                    "description": None,
                    "item_type": "task",
                    "priority": "normal",
                    "topic_id": str(topic_id),
                    "person_ids": [],
                    "date_changed": True,
                    "local_date": "2026-08-04",
                    "local_time": "11:45",
                },
            )
            assert scheduled.status_code == 200, scheduled.text
            assert scheduled.json()["work_item"]["due_at"] == "2026-08-04T11:45:00Z"

            edited = await client.patch(
                f"/api/v1/inbox/drafts/{draft_id}/items/{item_id}",
                headers=write_headers(csrf),
                json={
                    "expected_revision": draft_entry["revision"],
                    "item_type": "task",
                    "title": "Prepare release package",
                    "description": "Ready for an explicit confirmation",
                    "priority": "high",
                    "topic_id": str(topic_id),
                    "person_ids": [str(person_id)],
                    "local_date": "2026-08-03",
                    "local_time": "14:30",
                },
            )
            assert edited.status_code == 200, edited.text
            edited_body = edited.json()
            assert edited_body["items"][0]["topic"]["id"] == str(topic_id)
            assert edited_body["items"][0]["people"][0]["id"] == str(person_id)
            rejected = await client.post(
                f"/api/v1/inbox/drafts/{draft_id}/actions",
                headers=write_headers(csrf),
                json={
                    "action": "confirm",
                    "expected_revision": edited_body["revision"],
                    "accept_uncertainty": False,
                },
            )
            assert rejected.status_code == 409
            confirmed = await client.post(
                f"/api/v1/inbox/drafts/{draft_id}/actions",
                headers=write_headers(csrf),
                json={
                    "action": "confirm",
                    "expected_revision": edited_body["revision"],
                    "accept_uncertainty": True,
                },
            )
            assert confirmed.status_code == 200, confirmed.text
            converted_id = confirmed.json()["work_item_ids"][0]
            assert (
                await client.post(
                    f"/api/v1/inbox/notes/{foreign_note_id}/actions",
                    headers=write_headers(csrf),
                    json={"action": "archive"},
                )
            ).status_code == 404

            async with AsyncSession(database_engine) as session:
                converted = await session.get(WorkItem, converted_id)
                assert converted is not None
                assert converted.topic_id == topic_id
                assert converted.priority == "high"
                linked_people = set(
                    await session.scalars(
                        select(WorkItemPerson.person_id).where(
                            WorkItemPerson.work_item_id == converted.id
                        )
                    )
                )
                assert linked_people == {person_id}


@pytest.mark.integration
async def test_planner_timeline_and_settings_workflow(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    provider_secret = "provider-secret-must-never-reach-the-browser"
    app = create_app(
        settings=auth_settings(
            app_timezone="UTC",
            ai_provider="openai",
            ai_model="configured-ai-model",
            speech_provider="openai",
            speech_model="configured-speech-model",
            openai_api_key=provider_secret,
        ),
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
                topic = await create_topic(session, user.id, "Migration")
                person = await create_person(session, user.id, "Alex")
                task = await create_work_item(
                    session,
                    user.id,
                    item_type="task",
                    title="Move board",
                    topic_id=topic.id,
                    due_at=datetime.now(UTC) + timedelta(days=1),
                )
                await link_person_to_work_item(session, user.id, task.id, person.id)
                foreign = await create_telegram_user(session, FOREIGN_TELEGRAM_USER_ID)
                foreign_topic = await create_topic(session, foreign.id, "Secret")
                task_id = task.id
                topic_id = topic.id
                person_id = person.id
                foreign_topic_id = foreign_topic.id
                await session.commit()

            queue = await client.get("/api/v1/planner-queue")
            assert queue.status_code == 200
            queued = queue.json()["items"][0]
            assert queued["planner_status"] == "needs_transfer"
            transferred = await client.post(
                f"/api/v1/work-items/{task_id}/actions",
                headers=write_headers(csrf),
                json={
                    "action": "planner_transferred",
                    "client_action_id": str(uuid4()),
                    "expected_revision": queued["item"]["revision"],
                },
            )
            assert transferred.status_code == 200, transferred.text
            transferred_queue = await client.get(
                "/api/v1/planner-queue?status=transferred"
            )
            transferred_entry = transferred_queue.json()["items"][0]
            transfer_timestamp = transferred_entry["transferred_at"]
            assert transfer_timestamp is not None

            edited = await client.post(
                f"/api/v1/work-items/{task_id}/actions",
                headers=write_headers(csrf),
                json={
                    "action": "edit",
                    "client_action_id": str(uuid4()),
                    "expected_revision": transferred_entry["item"]["revision"],
                    "title": "Move board safely",
                    "description": "Manual Planner workflow",
                    "item_type": "task",
                    "priority": "urgent",
                    "topic_id": str(topic_id),
                    "person_ids": [str(person_id)],
                },
            )
            assert edited.status_code == 200, edited.text
            update_queue = await client.get(
                "/api/v1/planner-queue?status=update_required"
            )
            updated_entry = update_queue.json()["items"][0]
            assert updated_entry["transferred_at"] == transfer_timestamp

            completed = await client.post(
                f"/api/v1/work-items/{task_id}/actions",
                headers=write_headers(csrf),
                json={
                    "action": "complete",
                    "client_action_id": str(uuid4()),
                    "expected_revision": updated_entry["item"]["revision"],
                },
            )
            assert completed.status_code == 200, completed.text
            reopened = await client.post(
                f"/api/v1/work-items/{task_id}/actions",
                headers=write_headers(csrf),
                json={
                    "action": "reopen",
                    "client_action_id": str(uuid4()),
                    "expected_revision": completed.json()["work_item"]["revision"],
                },
            )
            assert reopened.status_code == 200, reopened.text
            restored = await client.get("/api/v1/planner-queue?status=update_required")
            assert restored.json()["items"][0]["transferred_at"] == transfer_timestamp

            timeline = await client.get(
                "/api/v1/timeline",
                params={
                    "topic_id": str(topic_id),
                    "person_id": str(person_id),
                    "event_type": "planner_status_changed",
                    "work_item_type": "task",
                },
            )
            assert timeline.status_code == 200
            assert len(timeline.json()["items"]) >= 3
            assert all(
                set(event)
                == {
                    "id",
                    "entity_kind",
                    "entity_id",
                    "event_type",
                    "occurred_at",
                    "title",
                    "work_item_type",
                    "status",
                    "topics",
                    "people",
                }
                for event in timeline.json()["items"]
            )
            assert "payload" not in timeline.text

            settings = await client.get("/api/v1/settings")
            assert set(settings.json()["providers"]) == {
                "ai_configured",
                "speech_configured",
            }
            assert "api_key" not in settings.text.casefold()
            assert provider_secret not in settings.text
            assert "configured-ai-model" not in settings.text
            assert "configured-speech-model" not in settings.text
            invalid = await client.put(
                "/api/v1/settings/preferences",
                headers=write_headers(csrf),
                json={
                    **settings.json()["preferences"],
                    "timezone": "Not/A_Timezone",
                },
            )
            assert invalid.status_code == 422
            preferences = {
                **settings.json()["preferences"],
                "timezone": "Europe/Riga",
                "quiet_hours_enabled": True,
                "quiet_hours_start": "22:30",
                "quiet_hours_end": "07:15",
                "date_display_format": "year_month_day",
                "time_display_format": "12h",
            }
            assert (
                await client.put(
                    "/api/v1/settings/preferences",
                    headers={"Origin": ORIGIN},
                    json=preferences,
                )
            ).status_code == 403
            saved = await client.put(
                "/api/v1/settings/preferences",
                headers=write_headers(csrf),
                json=preferences,
            )
            assert saved.status_code == 200, saved.text
            assert saved.json()["preferences"]["timezone"] == "Europe/Riga"
            assert saved.json()["preferences"]["quiet_hours_start"] == "22:30:00"

            created_person = await client.post(
                "/api/v1/people",
                headers=write_headers(csrf),
                json={
                    "display_name": "Maria Petrova",
                    "role": "Lead",
                    "notes": None,
                    "aliases": [" Masha ", "masha", "Maria Petrova"],
                    "is_active": True,
                },
            )
            assert created_person.status_code == 201
            assert created_person.json()["aliases"] == ["masha"]
            hidden = await client.patch(
                f"/api/v1/settings/topics/{foreign_topic_id}",
                headers=write_headers(csrf),
                json={
                    "name": "Visible",
                    "description": None,
                    "aliases": [],
                    "is_active": False,
                },
            )
            assert hidden.status_code == 404


@pytest.mark.integration
async def test_inbox_bulk_action_rolls_back_as_one_transaction(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(),
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
                first = Note(user_id=user.id, content="First", source="manual")
                second = Note(user_id=user.id, content="Second", source="manual")
                foreign = await create_telegram_user(session, FOREIGN_TELEGRAM_USER_ID)
                private = Note(user_id=foreign.id, content="Private", source="manual")
                session.add_all([first, second, private])
                await session.flush()
                first_id, second_id, private_id = first.id, second.id, private.id
                await session.commit()

            failed = await client.post(
                "/api/v1/inbox/bulk-actions",
                headers=write_headers(csrf),
                json={
                    "action": "archive",
                    "entries": [
                        {"kind": "note", "id": str(first_id)},
                        {"kind": "note", "id": str(private_id)},
                    ],
                },
            )
            assert failed.status_code == 404
            async with AsyncSession(database_engine) as session:
                assert (
                    await session.get(Note, first_id)
                ).inbox_disposition == "pending"  # type: ignore[union-attr]

            succeeded = await client.post(
                "/api/v1/inbox/bulk-actions",
                headers=write_headers(csrf),
                json={
                    "action": "archive",
                    "entries": [
                        {"kind": "note", "id": str(first_id)},
                        {"kind": "note", "id": str(second_id)},
                    ],
                },
            )
            assert succeeded.status_code == 200
            assert succeeded.json() == {"processed": 2}
