from collections.abc import AsyncIterator

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from flowmate.api.app import create_app
from flowmate.api.routes.remaining.inbox import router as inbox_router
from flowmate.api.routes.remaining.planning import router as planning_router
from flowmate.api.routes.remaining.settings import router as settings_router
from flowmate.db.models import (
    User,
)
from flowmate.db.users import create_telegram_user
from flowmate.task_engine.service import (
    create_topic,
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
async def test_settings_preferences_people_and_topics_workflow(
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
                foreign = await create_telegram_user(session, FOREIGN_TELEGRAM_USER_ID)
                foreign_topic = await create_topic(session, foreign.id, "Secret")
                foreign_topic_id = foreign_topic.id
                await session.commit()

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


def test_remaining_public_routes_are_stable() -> None:
    actual = {
        (method, f"/api/v1{route.path}")
        for router in (inbox_router, planning_router, settings_router)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
    }
    assert actual == {
        ("GET", "/api/v1/inbox"),
        ("GET", "/api/v1/inbox/drafts/{draft_id}"),
        ("PATCH", "/api/v1/inbox/drafts/{draft_id}/items/{item_id}"),
        ("POST", "/api/v1/inbox/drafts/{draft_id}/actions"),
        ("POST", "/api/v1/inbox/notes/{note_id}/actions"),
        ("POST", "/api/v1/inbox/bulk-actions"),
        ("GET", "/api/v1/planner-queue"),
        ("GET", "/api/v1/timeline"),
        ("GET", "/api/v1/settings"),
        ("PUT", "/api/v1/settings/preferences"),
        ("GET", "/api/v1/settings/topics"),
        ("POST", "/api/v1/topics"),
        ("PATCH", "/api/v1/settings/topics/{topic_id}"),
        ("GET", "/api/v1/settings/people"),
        ("POST", "/api/v1/people"),
        ("PATCH", "/api/v1/settings/people/{person_id}"),
    }
