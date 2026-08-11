from collections.abc import AsyncIterator
from datetime import UTC
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from flowmate.api.app import create_app
from flowmate.db.drafts import create_parsing_draft, replace_draft_analysis
from flowmate.db.models import (
    AIProcessingJob,
    AuditEvent,
    DraftItemRecord,
    DraftSession,
    Note,
    NoteLink,
    User,
    WorkItem,
    WorkItemPerson,
)
from flowmate.db.notes import create_note_idempotently
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.service import (
    create_person,
    create_topic,
    create_work_item,
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


@pytest.mark.integration
async def test_inbox_permanently_deletes_pending_note_and_draft_tree(
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
            secret = "private-delete-test-content"
            async with AsyncSession(database_engine) as session:
                user = await session.scalar(
                    select(User).where(User.telegram_user_id == TELEGRAM_USER_ID)
                )
                assert user is not None
                standalone = Note(user_id=user.id, content=secret, source="manual")
                source = Note(
                    user_id=user.id, content=f"{secret}-draft", source="manual"
                )
                session.add_all([standalone, source])
                await session.flush()
                draft = await create_parsing_draft(
                    session,
                    user_id=user.id,
                    source_note_id=source.id,
                    ttl_hours=24,
                )
                await replace_draft_analysis(
                    session,
                    draft,
                    make_analysis_result(
                        make_parse_result([make_draft_item(title="Delete draft")])
                    ),
                    question=None,
                    ttl_hours=24,
                )
                job = AIProcessingJob(
                    user_id=user.id,
                    job_kind="draft_parse",
                    entity_id=draft.id,
                    operation_key="delete-test",
                    prompt_name="draft_parse",
                    prompt_version="test-v1",
                    input_text=secret,
                    input_source="text",
                )
                session.add(job)
                await session.flush()
                user_id = user.id
                standalone_id = standalone.id
                source_id = source.id
                draft_id = draft.id
                draft_item_id = draft.items[0].id
                job_id = job.id
                await session.commit()

            inbox = await client.get("/api/v1/inbox?kind=draft")
            draft_entry = next(
                value for value in inbox.json()["items"] if value["id"] == str(draft_id)
            )
            note_deleted = await client.post(
                f"/api/v1/inbox/notes/{standalone_id}/actions",
                headers=write_headers(csrf),
                json={"action": "delete"},
            )
            assert note_deleted.status_code == 200, note_deleted.text
            assert note_deleted.json() == {
                "status": "deleted",
                "id": str(standalone_id),
            }
            draft_deleted = await client.post(
                f"/api/v1/inbox/drafts/{draft_id}/actions",
                headers=write_headers(csrf),
                json={
                    "action": "delete",
                    "expected_revision": draft_entry["revision"],
                },
            )
            assert draft_deleted.status_code == 200, draft_deleted.text
            assert draft_deleted.json() == {
                "status": "deleted",
                "id": str(source_id),
                "draft_id": str(draft_id),
            }

            async with AsyncSession(database_engine) as session:
                assert await session.get(Note, standalone_id) is None
                assert await session.get(Note, source_id) is None
                assert await session.get(DraftSession, draft_id) is None
                assert await session.get(DraftItemRecord, draft_item_id) is None
                assert await session.get(AIProcessingJob, job_id) is None
                events = list(
                    await session.scalars(
                        select(AuditEvent)
                        .where(
                            AuditEvent.user_id == user_id,
                            AuditEvent.action == "note.deleted",
                        )
                        .order_by(AuditEvent.created_at)
                    )
                )
                assert [event.entity_id for event in events] == [
                    standalone_id,
                    source_id,
                ]
                assert [event.safe_metadata["category"] for event in events] == [
                    "standalone",
                    "draft_source",
                ]
                assert secret not in repr([event.safe_metadata for event in events])


@pytest.mark.integration
async def test_inbox_delete_guards_and_bulk_rollback(
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
                foreign = await create_telegram_user(session, FOREIGN_TELEGRAM_USER_ID)
                topic = await create_topic(session, user.id, "Delete guard")
                first = Note(user_id=user.id, content="First", source="manual")
                second = Note(user_id=user.id, content="Second", source="manual")
                linked = Note(user_id=user.id, content="Linked", source="manual")
                sourced = Note(user_id=user.id, content="Sourced", source="manual")
                kept = Note(
                    user_id=user.id,
                    content="Kept",
                    source="manual",
                    inbox_disposition="kept",
                )
                archived = Note(
                    user_id=user.id,
                    content="Archived",
                    source="manual",
                    inbox_disposition="archived",
                )
                private = Note(user_id=foreign.id, content="Private", source="manual")
                session.add_all(
                    [first, second, linked, sourced, kept, archived, private]
                )
                await session.flush()
                session.add(
                    NoteLink(
                        user_id=user.id,
                        note_id=linked.id,
                        topic_id=topic.id,
                    )
                )
                await create_work_item(
                    session,
                    user.id,
                    item_type="task",
                    title="Keeps provenance",
                    source_note_id=sourced.id,
                )
                closed_source = Note(
                    user_id=user.id, content="Closed source", source="manual"
                )
                session.add(closed_source)
                await session.flush()
                closed = await create_parsing_draft(
                    session,
                    user_id=user.id,
                    source_note_id=closed_source.id,
                    ttl_hours=24,
                )
                closed.status = "cancelled"
                await session.flush()
                stale_source = Note(
                    user_id=user.id, content="Stale source", source="manual"
                )
                session.add(stale_source)
                await session.flush()
                stale = await create_parsing_draft(
                    session,
                    user_id=user.id,
                    source_note_id=stale_source.id,
                    ttl_hours=24,
                )
                await session.flush()
                ids = {
                    "first": first.id,
                    "second": second.id,
                    "linked": linked.id,
                    "sourced": sourced.id,
                    "kept": kept.id,
                    "archived": archived.id,
                    "private": private.id,
                    "closed": closed.id,
                    "stale": stale.id,
                }
                closed_updated_at = await session.scalar(
                    select(DraftSession.updated_at).where(DraftSession.id == closed.id)
                )
                assert closed_updated_at is not None
                closed_revision = int(
                    closed_updated_at.astimezone(UTC).timestamp() * 1_000_000
                )
                await session.commit()

            for note_id in (
                ids["kept"],
                ids["archived"],
                ids["linked"],
                ids["sourced"],
            ):
                response = await client.post(
                    f"/api/v1/inbox/notes/{note_id}/actions",
                    headers=write_headers(csrf),
                    json={"action": "delete"},
                )
                assert response.status_code == 409, response.text
            foreign_response = await client.post(
                f"/api/v1/inbox/notes/{ids['private']}/actions",
                headers=write_headers(csrf),
                json={"action": "delete"},
            )
            assert foreign_response.status_code == 404
            closed_response = await client.post(
                f"/api/v1/inbox/drafts/{ids['closed']}/actions",
                headers=write_headers(csrf),
                json={"action": "delete", "expected_revision": closed_revision},
            )
            assert closed_response.status_code == 409
            stale_response = await client.post(
                f"/api/v1/inbox/drafts/{ids['stale']}/actions",
                headers=write_headers(csrf),
                json={"action": "delete", "expected_revision": 0},
            )
            assert stale_response.status_code == 409

            failed_bulk = await client.post(
                "/api/v1/inbox/bulk-actions",
                headers=write_headers(csrf),
                json={
                    "action": "delete",
                    "entries": [
                        {"kind": "note", "id": str(ids["first"])},
                        {"kind": "note", "id": str(ids["linked"])},
                    ],
                },
            )
            assert failed_bulk.status_code == 409, failed_bulk.text
            async with AsyncSession(database_engine) as session:
                assert await session.get(Note, ids["first"]) is not None
                assert await session.get(Note, ids["linked"]) is not None

            succeeded_bulk = await client.post(
                "/api/v1/inbox/bulk-actions",
                headers=write_headers(csrf),
                json={
                    "action": "delete",
                    "entries": [
                        {"kind": "note", "id": str(ids["first"])},
                        {"kind": "note", "id": str(ids["second"])},
                    ],
                },
            )
            assert succeeded_bulk.status_code == 200, succeeded_bulk.text
            assert succeeded_bulk.json() == {"processed": 2}
            async with AsyncSession(database_engine) as session:
                assert await session.get(Note, ids["first"]) is None
                assert await session.get(Note, ids["second"]) is None
