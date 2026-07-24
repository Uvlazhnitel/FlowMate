from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from flowmate.api.app import create_app
from flowmate.auth.delivery import LoginCodeDeliveryError
from flowmate.auth.pwa import (
    InvalidLoginCodeError,
    InvalidPwaSessionError,
    LoginCodeRateLimitedError,
    get_authenticated_session,
    issue_login_code,
    verify_login_code,
)
from flowmate.core.config import Settings
from flowmate.db.models import PwaLoginCode, PwaSession, User
from tests.conftest import TEST_DATABASE_URL, started_app

TELEGRAM_USER_ID = 761_001
ORIGIN = "http://localhost:8080"


class CapturingLoginCodeSender:
    def __init__(self) -> None:
        self.codes: list[str] = []

    async def send_login_code(
        self,
        telegram_user_id: int,
        code: str,
        expires_in_minutes: int,
    ) -> None:
        assert telegram_user_id == TELEGRAM_USER_ID
        assert expires_in_minutes == 10
        self.codes.append(code)


class FailingLoginCodeSender:
    async def send_login_code(
        self,
        telegram_user_id: int,
        code: str,
        expires_in_minutes: int,
    ) -> None:
        raise LoginCodeDeliveryError


def auth_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "app_api_key": "test-secret",
        "database_url": TEST_DATABASE_URL,
        "telegram_bot_token": "123456:test-token",
        "telegram_allowed_user_ids": str(TELEGRAM_USER_ID),
        "pwa_telegram_user_id": TELEGRAM_USER_ID,
        "pwa_auth_secret": "test-pwa-auth-secret-not-for-production",
        "pwa_public_origin": ORIGIN,
    }
    values.update(overrides)
    return Settings(_env_file=None, **cast(Any, values))


async def cleanup_pwa_owner(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session:
        await session.execute(
            delete(User).where(User.telegram_user_id == TELEGRAM_USER_ID)
        )
        await session.commit()


@pytest.mark.integration
async def test_login_code_is_hashed_single_use_and_expiring(
    database_session: AsyncSession,
) -> None:
    sender = CapturingLoginCodeSender()
    settings = auth_settings()
    now = datetime(2026, 8, 1, 9, tzinfo=UTC)

    await issue_login_code(
        database_session,
        settings,
        sender,
        now=now,
        code="123456",
    )
    stored = await database_session.scalar(select(PwaLoginCode))
    assert stored is not None
    assert stored.code_digest != "123456"
    assert "123456" not in repr(stored)

    created = await verify_login_code(
        database_session,
        settings,
        "123456",
        now=now + timedelta(minutes=1),
        token="session-token",
        csrf_token="csrf-token",
    )
    assert created.user.telegram_user_id == TELEGRAM_USER_ID
    assert created.session.token_digest != "session-token"
    with pytest.raises(InvalidLoginCodeError):
        await verify_login_code(
            database_session,
            settings,
            "123456",
            now=now + timedelta(minutes=2),
        )

    await issue_login_code(
        database_session,
        settings,
        sender,
        now=now + timedelta(minutes=15),
        code="654321",
    )
    with pytest.raises(InvalidLoginCodeError):
        await verify_login_code(
            database_session,
            settings,
            "654321",
            now=now + timedelta(minutes=26),
        )


@pytest.mark.integration
async def test_invalid_code_attempts_and_request_rate_limit(
    database_session: AsyncSession,
) -> None:
    sender = CapturingLoginCodeSender()
    settings = auth_settings()
    now = datetime(2026, 8, 2, 9, tzinfo=UTC)
    for index in range(3):
        await issue_login_code(
            database_session,
            settings,
            sender,
            now=now + timedelta(seconds=index),
            code=f"12345{index}",
        )
    with pytest.raises(LoginCodeRateLimitedError):
        await issue_login_code(
            database_session,
            settings,
            sender,
            now=now + timedelta(seconds=3),
            code="999999",
        )

    with pytest.raises(InvalidLoginCodeError) as captured:
        await verify_login_code(
            database_session,
            settings,
            "000000",
            now=now + timedelta(seconds=4),
        )
    assert captured.value.attempt_recorded is True
    latest = await database_session.scalar(
        select(PwaLoginCode).order_by(PwaLoginCode.created_at.desc()).limit(1)
    )
    assert latest is not None and latest.attempt_count == 1


@pytest.mark.integration
async def test_login_code_is_invalidated_after_maximum_attempts(
    database_session: AsyncSession,
) -> None:
    sender = CapturingLoginCodeSender()
    settings = auth_settings(pwa_login_max_attempts=2)
    now = datetime(2026, 8, 2, 10, tzinfo=UTC)
    await issue_login_code(database_session, settings, sender, now=now, code="123456")

    for offset in (1, 2):
        with pytest.raises(InvalidLoginCodeError):
            await verify_login_code(
                database_session,
                settings,
                "000000",
                now=now + timedelta(seconds=offset),
            )
    with pytest.raises(InvalidLoginCodeError):
        await verify_login_code(
            database_session,
            settings,
            "123456",
            now=now + timedelta(seconds=3),
        )

    stored = await database_session.scalar(select(PwaLoginCode))
    assert stored is not None
    assert stored.attempt_count == 2
    assert stored.invalidated_at is not None


@pytest.mark.integration
async def test_session_limit_revokes_oldest_session(
    database_session: AsyncSession,
) -> None:
    sender = CapturingLoginCodeSender()
    settings = auth_settings(pwa_max_active_sessions=2)
    now = datetime(2026, 8, 3, 9, tzinfo=UTC)
    tokens: list[str] = []
    for index in range(3):
        code = f"23456{index}"
        token = f"session-token-{index}"
        await issue_login_code(
            database_session,
            settings,
            sender,
            now=now + timedelta(minutes=index),
            code=code,
        )
        await verify_login_code(
            database_session,
            settings,
            code,
            now=now + timedelta(minutes=index, seconds=1),
            token=token,
            csrf_token=f"csrf-{index}",
        )
        tokens.append(token)

    active_count = await database_session.scalar(
        select(func.count(PwaSession.id)).where(PwaSession.revoked_at.is_(None))
    )
    assert active_count == 2
    with pytest.raises(InvalidPwaSessionError):
        await get_authenticated_session(database_session, tokens[0], now=now)


@pytest.mark.integration
async def test_pwa_auth_http_flow_and_csrf(
    database_engine: AsyncEngine,
) -> None:
    sender = CapturingLoginCodeSender()
    settings = auth_settings()
    app = create_app(
        settings=settings,
        engine=database_engine,
        login_code_sender=sender,
    )
    async with started_app(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            missing_origin = await client.post("/api/v1/auth/login-code")
            assert missing_origin.status_code == 403

            requested = await client.post(
                "/api/v1/auth/login-code",
                headers={"Origin": ORIGIN},
            )
            assert requested.status_code == 202
            assert requested.json() == {
                "status": "code_sent",
                "expires_in_seconds": 600,
            }

            authenticated = await client.post(
                "/api/v1/auth/session",
                headers={"Origin": ORIGIN},
                json={"code": sender.codes[-1]},
            )
            assert authenticated.status_code == 200
            cookie_headers = authenticated.headers.get_list("set-cookie")
            session_cookie = next(
                value
                for value in cookie_headers
                if value.startswith("flowmate_session=")
            )
            csrf_cookie = next(
                value for value in cookie_headers if value.startswith("flowmate_csrf=")
            )
            assert "HttpOnly" in session_cookie
            assert "SameSite=lax" in session_cookie
            assert "HttpOnly" not in csrf_cookie
            assert "SameSite=lax" in csrf_cookie
            assert client.cookies.get("flowmate_session")
            csrf_token = client.cookies.get("flowmate_csrf")
            assert csrf_token

            current = await client.get("/api/v1/auth/me")
            assert current.status_code == 200
            assert current.json()["display_name"] is None
            assert current.json()["date_display_format"] == "day_month_year"
            assert current.json()["time_display_format"] == "24h"

            rejected_logout = await client.delete(
                "/api/v1/auth/session",
                headers={"Origin": ORIGIN},
            )
            assert rejected_logout.status_code == 403
            logged_out = await client.delete(
                "/api/v1/auth/session",
                headers={"Origin": ORIGIN, "X-CSRF-Token": csrf_token},
            )
            assert logged_out.status_code == 204
            assert (await client.get("/api/v1/auth/me")).status_code == 401
            async with AsyncSession(database_engine) as session:
                stored_session = await session.scalar(
                    select(PwaSession).order_by(PwaSession.created_at.desc()).limit(1)
                )
                assert stored_session is not None
                assert stored_session.revoked_at is not None


@pytest.mark.integration
async def test_secure_auth_cookie_flags(database_engine: AsyncEngine) -> None:
    sender = CapturingLoginCodeSender()
    app = create_app(
        settings=auth_settings(pwa_cookie_secure=True),
        engine=database_engine,
        login_code_sender=sender,
    )
    async with started_app(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="https://test"
        ) as client:
            await client.post("/api/v1/auth/login-code", headers={"Origin": ORIGIN})
            authenticated = await client.post(
                "/api/v1/auth/session",
                headers={"Origin": ORIGIN},
                json={"code": sender.codes[-1]},
            )

    assert authenticated.status_code == 200
    cookie_headers = authenticated.headers.get_list("set-cookie")
    session_cookie = next(
        value for value in cookie_headers if value.startswith("flowmate_session=")
    )
    csrf_cookie = next(
        value for value in cookie_headers if value.startswith("flowmate_csrf=")
    )
    assert "Secure" in session_cookie and "HttpOnly" in session_cookie
    assert "Secure" in csrf_cookie and "HttpOnly" not in csrf_cookie
    await cleanup_pwa_owner(database_engine)


@pytest.mark.integration
async def test_all_pwa_routes_reject_unauthenticated_requests(
    database_engine: AsyncEngine,
) -> None:
    identifier = "a6849f37-32e2-43bf-a78a-d4087762643f"
    read_paths = [
        "/api/v1/auth/me",
        "/api/v1/dashboard",
        "/api/v1/today?section=overdue",
        "/api/v1/topics",
        f"/api/v1/topics/{identifier}",
        f"/api/v1/topics/{identifier}/content?section=active",
        "/api/v1/people",
        f"/api/v1/people/{identifier}",
        f"/api/v1/people/{identifier}/content?section=follow_ups",
        "/api/v1/agenda",
        "/api/v1/inbox",
        f"/api/v1/inbox/drafts/{identifier}",
        "/api/v1/planner-queue",
        "/api/v1/timeline",
        "/api/v1/settings",
        "/api/v1/settings/topics",
        "/api/v1/settings/people",
        "/api/v1/meetings/active",
        "/api/v1/meetings",
        f"/api/v1/meetings/{identifier}",
        f"/api/v1/meetings/{identifier}/captures",
        f"/api/v1/meetings/{identifier}/review",
    ]
    write_requests: list[tuple[str, str, dict[str, object] | None]] = [
        ("DELETE", "/api/v1/auth/session", None),
        ("PUT", "/api/v1/workspace", {"workspace": "work"}),
        (
            "POST",
            f"/api/v1/work-items/{identifier}/actions",
            {
                "action": "complete",
                "client_action_id": identifier,
                "expected_revision": 0,
            },
        ),
        (
            "PATCH",
            f"/api/v1/inbox/drafts/{identifier}/items/{identifier}",
            {
                "expected_revision": 0,
                "item_type": "task",
                "title": "Protected",
                "priority": "normal",
            },
        ),
        (
            "POST",
            f"/api/v1/inbox/drafts/{identifier}/actions",
            {"action": "cancel", "expected_revision": 0},
        ),
        (
            "POST",
            f"/api/v1/inbox/notes/{identifier}/actions",
            {"action": "keep"},
        ),
        (
            "POST",
            "/api/v1/inbox/bulk-actions",
            {"action": "keep", "entries": [{"kind": "note", "id": identifier}]},
        ),
        (
            "PUT",
            "/api/v1/settings/preferences",
            {
                "timezone": "UTC",
                "morning_digest_enabled": False,
                "morning_digest_time": "09:00",
                "evening_digest_enabled": False,
                "evening_digest_time": "18:00",
                "quiet_hours_enabled": False,
                "quiet_hours_start": "22:00",
                "quiet_hours_end": "08:00",
                "default_snooze_minutes": 60,
                "send_empty_digests": False,
                "date_display_format": "day_month_year",
                "time_display_format": "24h",
            },
        ),
        (
            "POST",
            "/api/v1/topics",
            {"name": "Protected", "aliases": [], "is_active": True},
        ),
        (
            "PATCH",
            f"/api/v1/settings/topics/{identifier}",
            {"name": "Protected", "aliases": [], "is_active": True},
        ),
        (
            "POST",
            "/api/v1/people",
            {"display_name": "Protected", "aliases": [], "is_active": True},
        ),
        (
            "PATCH",
            f"/api/v1/settings/people/{identifier}",
            {"display_name": "Protected", "aliases": [], "is_active": True},
        ),
        (
            "POST",
            "/api/v1/meetings",
            {
                "client_action_id": identifier,
                "type": "team",
                "participant_ids": [],
                "topic_ids": [],
            },
        ),
        (
            "POST",
            f"/api/v1/meetings/{identifier}/actions",
            {
                "action": "end",
                "client_action_id": identifier,
                "expected_revision": 0,
            },
        ),
        (
            "PATCH",
            f"/api/v1/meetings/{identifier}/captures/{identifier}/items/{identifier}",
            {
                "expected_revision": 0,
                "item_type": "task",
                "title": "Protected",
            },
        ),
        (
            "POST",
            f"/api/v1/meetings/{identifier}/captures/{identifier}/actions",
            {
                "action": "remove",
                "client_action_id": identifier,
                "expected_revision": 0,
            },
        ),
        (
            "POST",
            f"/api/v1/meetings/{identifier}/review/actions",
            {
                "action": "confirm_ready",
                "client_action_id": identifier,
                "expected_revision": 0,
            },
        ),
        (
            "POST",
            f"/api/v1/meetings/{identifier}/review/items/{identifier}/actions",
            {"action": "exclude", "expected_revision": 0},
        ),
        (
            "POST",
            f"/api/v1/meetings/{identifier}/review/items/{identifier}/clarification",
            {"answer": "Protected", "expected_revision": 0},
        ),
        (
            "POST",
            f"/api/v1/meetings/{identifier}/review/agenda/{identifier}",
            {"outcome": "discussed"},
        ),
        (
            "POST",
            f"/api/v1/meetings/{identifier}/review/agenda",
            {"title": "Protected"},
        ),
    ]
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
            for path in read_paths:
                response = await client.get(path)
                assert response.status_code == 401, path
            for method, path, payload in write_requests:
                response = await client.request(method, path, json=payload)
                assert response.status_code == 401, f"{method} {path}: {response.text}"

            await client.post("/api/v1/auth/login-code", headers={"Origin": ORIGIN})
            await client.post(
                "/api/v1/auth/session",
                headers={"Origin": ORIGIN},
                json={"code": sender.codes[-1]},
            )
            csrf = client.cookies.get("flowmate_csrf")
            assert csrf is not None
            for method, path, payload in write_requests:
                missing_csrf = await client.request(
                    method, path, headers={"Origin": ORIGIN}, json=payload
                )
                assert missing_csrf.status_code == 403, f"{method} {path}"
                foreign_origin = await client.request(
                    method,
                    path,
                    headers={
                        "Origin": "https://attacker.example.com",
                        "X-CSRF-Token": csrf,
                    },
                    json=payload,
                )
                assert foreign_origin.status_code == 403, f"{method} {path}"
    await cleanup_pwa_owner(database_engine)


@pytest.mark.integration
async def test_login_delivery_failure_rolls_back_code_and_owner(
    database_engine: AsyncEngine,
) -> None:
    telegram_user_id = TELEGRAM_USER_ID + 1
    settings = auth_settings(
        pwa_telegram_user_id=telegram_user_id,
        telegram_allowed_user_ids=str(telegram_user_id),
    )
    app = create_app(
        settings=settings,
        engine=database_engine,
        login_code_sender=FailingLoginCodeSender(),
    )
    async with started_app(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/auth/login-code",
                headers={"Origin": ORIGIN},
            )
    assert response.status_code == 503

    async with AsyncSession(database_engine) as session:
        owner_count = await session.scalar(
            select(func.count(User.id)).where(User.telegram_user_id == telegram_user_id)
        )
        assert owner_count == 0
