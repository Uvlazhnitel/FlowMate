import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from flowmate.ai.errors import AITimeoutError
from flowmate.ai.schemas import DraftParseResult
from flowmate.api.app import create_app
from flowmate.db.models import DraftSession, Note, User, WorkItem
from flowmate.db.users import create_telegram_user
from flowmate.workspaces import workspace_context
from tests.ai_factories import (
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


class StubProvider:
    def __init__(
        self,
        result: DraftParseResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or make_parse_result()
        self.error = error
        self.calls = 0

    async def parse(self, *, system_prompt: str, user_text: str) -> DraftParseResult:
        assert system_prompt
        assert user_text
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
async def cleanup_capture_users(database_engine: AsyncEngine) -> AsyncIterator[None]:
    yield
    async with AsyncSession(database_engine) as session:
        await session.execute(
            delete(User).where(
                User.telegram_user_id.in_((TELEGRAM_USER_ID, TELEGRAM_USER_ID + 1))
            )
        )
        await session.commit()


def capture_payload(capture_id: UUID, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "text": "Позвонить врачу",
        "workspace": "personal",
        "target_bucket": "auto",
        "client_capture_id": str(capture_id),
    }
    payload.update(overrides)
    return payload


@pytest.mark.integration
async def test_text_capture_requires_session_csrf_origin_and_valid_payload(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    provider = StubProvider()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
        ai_provider=provider,
    )
    async with started_app(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            payload = capture_payload(uuid4())
            unauthorized = await client.post("/api/v1/captures/text", json=payload)
            assert unauthorized.status_code == 401
            csrf = await authenticated_client(client, sender)
            missing_csrf = await client.post("/api/v1/captures/text", json=payload)
            assert missing_csrf.status_code == 403
            assert (
                await client.post(
                    "/api/v1/captures/text",
                    headers={"Origin": "https://wrong.example", "X-CSRF-Token": csrf},
                    json=payload,
                )
            ).status_code == 403
            invalid = await client.post(
                "/api/v1/captures/text",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json=capture_payload(uuid4(), text="   "),
            )
            assert invalid.status_code == 422
            assert provider.calls == 0


@pytest.mark.integration
async def test_text_capture_reports_missing_ai_provider(
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
            response = await client.post(
                "/api/v1/captures/text",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
                json=capture_payload(uuid4()),
            )
            assert response.status_code == 503


@pytest.mark.integration
async def test_confident_capture_is_atomic_idempotent_and_honors_bucket(
    database_engine: AsyncEngine,
) -> None:
    parsed_due = datetime.now(UTC) + timedelta(days=7)
    provider = StubProvider(
        make_parse_result(
            [
                make_draft_item(
                    title="Позвонить врачу",
                    due_date_candidate=make_temporal_candidate(
                        normalized_value=parsed_due
                    ),
                )
            ]
        )
    )
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
        ai_provider=provider,
    )
    capture_id = uuid4()
    async with started_app(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await authenticated_client(client, sender)
            payload = capture_payload(capture_id, target_bucket="tomorrow")
            headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
            created = await client.post(
                "/api/v1/captures/text", headers=headers, json=payload
            )
            assert created.status_code == 201, created.text
            assert created.json()["duplicate"] is False
            assert created.json()["disposition"] == "created"
            assert len(created.json()["work_item_ids"]) == 1

            duplicate = await client.post(
                "/api/v1/captures/text", headers=headers, json=payload
            )
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json() == {**created.json(), "duplicate": True}
            assert provider.calls == 1

            mismatch = await client.post(
                "/api/v1/captures/text",
                headers=headers,
                json={**payload, "target_bucket": "today"},
            )
            assert mismatch.status_code == 409

            async with AsyncSession(database_engine) as session:
                note = await session.get(Note, capture_id)
                item = await session.get(
                    WorkItem, UUID(created.json()["work_item_ids"][0])
                )
                draft = await session.get(
                    DraftSession, UUID(created.json()["draft_id"])
                )
                assert note is not None and note.source == "manual"
                assert note.telegram_update_id is None
                assert item is not None and item.source_note_id == capture_id
                assert item.workspace == "personal"
                assert item.status == "planned"
                assert item.due_at is not None
                tomorrow = (datetime.now(UTC) + timedelta(days=1)).date()
                assert item.due_at.date() == tomorrow
                assert draft is not None and draft.status == "confirmed"


@pytest.mark.integration
async def test_ambiguous_capture_stays_in_inbox_and_blocks_another_open_draft(
    database_engine: AsyncEngine,
) -> None:
    provider = StubProvider(
        make_parse_result(
            [make_draft_item(confidence=0.6, missing_fields=["due_date"])]
        )
    )
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
        ai_provider=provider,
    )
    async with started_app(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await authenticated_client(client, sender)
            headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
            first = await client.post(
                "/api/v1/captures/text",
                headers=headers,
                json=capture_payload(uuid4(), workspace="work"),
            )
            assert first.status_code == 201, first.text
            assert first.json()["disposition"] == "inbox"
            assert first.json()["work_item_ids"] == []

            blocked = await client.post(
                "/api/v1/captures/text",
                headers=headers,
                json=capture_payload(uuid4(), text="Другая задача", workspace="work"),
            )
            assert blocked.status_code == 409
            assert provider.calls == 1
            async with AsyncSession(database_engine) as session:
                draft = await session.get(DraftSession, UUID(first.json()["draft_id"]))
                assert draft is not None
                assert draft.workspace == "work"
                assert draft.status in {"needs_clarification", "ready"}


@pytest.mark.integration
async def test_text_capture_rolls_back_ai_errors_and_hides_foreign_key_collisions(
    database_engine: AsyncEngine,
) -> None:
    provider = StubProvider(error=AITimeoutError("provider details"))
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
        ai_provider=provider,
    )
    failed_id = uuid4()
    foreign_id = uuid4()
    async with started_app(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await authenticated_client(client, sender)
            headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
            failed = await client.post(
                "/api/v1/captures/text",
                headers=headers,
                json=capture_payload(failed_id),
            )
            assert failed.status_code == 502
            assert "provider details" not in failed.text

            async with AsyncSession(database_engine) as session:
                assert await session.get(Note, failed_id) is None
                foreign = await create_telegram_user(session, TELEGRAM_USER_ID + 1)
                with workspace_context(
                    session, user_id=foreign.id, workspace="personal"
                ):
                    session.add(
                        Note(
                            id=foreign_id,
                            user_id=foreign.id,
                            content="Чужая запись",
                            source="manual",
                        )
                    )
                await session.commit()

            collision = await client.post(
                "/api/v1/captures/text",
                headers=headers,
                json=capture_payload(foreign_id),
            )
            assert collision.status_code == 409
            assert "Чужая запись" not in collision.text


@pytest.mark.integration
async def test_concurrent_capture_reuses_one_conversion(
    database_engine: AsyncEngine,
) -> None:
    provider = StubProvider()
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(app_timezone="UTC"),
        engine=database_engine,
        login_code_sender=sender,
        ai_provider=provider,
    )
    async with started_app(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await authenticated_client(client, sender)
            headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
            payload = capture_payload(uuid4())
            first, second = await asyncio.gather(
                client.post("/api/v1/captures/text", headers=headers, json=payload),
                client.post("/api/v1/captures/text", headers=headers, json=payload),
            )
            assert sorted((first.status_code, second.status_code)) == [200, 201]
            assert first.json()["work_item_ids"] == second.json()["work_item_ids"]
            assert provider.calls == 1
