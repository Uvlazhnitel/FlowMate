from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from flowmate.api.app import create_app
from flowmate.db.models import (
    User,
)
from flowmate.db.users import create_telegram_user
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
                await create_topic(session, foreign.id, "Secret")
                task_id = task.id
                topic_id = topic.id
                person_id = person.id
                await session.commit()

            queue = await client.get("/api/v1/planner-queue")
            assert queue.status_code == 200
            assert queue.json()["items"] == []
            inbox = await client.get("/api/v1/inbox?kind=work_item")
            inbox_item = next(
                entry["item"]
                for entry in inbox.json()["items"]
                if entry["item"]["id"] == str(task_id)
            )
            stale = await client.post(
                f"/api/v1/work-items/{task_id}/actions",
                headers=write_headers(csrf),
                json={
                    "action": "planner_needs_transfer",
                    "client_action_id": str(uuid4()),
                    "expected_revision": inbox_item["revision"] - 1,
                },
            )
            assert stale.status_code == 409
            assert (await client.get("/api/v1/planner-queue")).json()["items"] == []
            added = await client.post(
                f"/api/v1/work-items/{task_id}/actions",
                headers=write_headers(csrf),
                json={
                    "action": "planner_needs_transfer",
                    "client_action_id": str(uuid4()),
                    "expected_revision": inbox_item["revision"],
                },
            )
            assert added.status_code == 200, added.text
            assert added.json()["work_item"]["planner_status"] == "needs_transfer"
            queue = await client.get("/api/v1/planner-queue")
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
