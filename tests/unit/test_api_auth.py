import io
import logging
from typing import Any, cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from flowmate.api.app import create_app
from flowmate.core.config import Settings
from tests.conftest import started_app


def create_test_app(**settings_overrides: object) -> FastAPI:
    values: dict[str, object] = {
        "app_api_key": "test-secret",
    }
    values.update(settings_overrides)
    return create_app(settings=Settings(_env_file=None, **cast(Any, values)))


async def request(
    path: str,
    *,
    app: FastAPI | None = None,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    raise_app_exceptions: bool = True,
) -> Response:
    test_app = app or create_test_app()
    async with started_app(test_app):
        transport = ASGITransport(
            app=test_app, raise_app_exceptions=raise_app_exceptions
        )
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, headers=headers)


async def test_me_rejects_missing_token_with_error_envelope() -> None:
    response = await request("/api/v1/me", headers={"X-Request-ID": "auth-request"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {
        "error": {
            "code": "unauthorized",
            "message": "Invalid authentication credentials",
            "request_id": "auth-request",
        }
    }


async def test_me_rejects_invalid_token() -> None:
    response = await request(
        "/api/v1/me", headers={"Authorization": "Bearer wrong-secret"}
    )

    assert response.status_code == 401


async def test_status_accepts_valid_token() -> None:
    response = await request(
        "/api/v1/status", headers={"Authorization": "Bearer test-secret"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "flowmate"}


async def test_me_describes_single_user_session() -> None:
    response = await request(
        "/api/v1/me", headers={"Authorization": "Bearer test-secret"}
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": "single-user",
        "authentication": "api-key",
    }


async def test_request_logs_do_not_expose_api_key() -> None:
    api_key = "private-request-api-key"
    app = create_test_app(app_api_key=api_key)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    request_logger = logging.getLogger("flowmate.api.middleware")
    request_logger.addHandler(handler)
    try:
        response = await request(
            "/api/v1/me",
            app=app,
            headers={"Authorization": f"Bearer {api_key}"},
        )
    finally:
        request_logger.removeHandler(handler)

    assert response.status_code == 200
    assert api_key not in stream.getvalue()


async def test_middleware_adds_request_id_and_security_headers() -> None:
    response = await request(
        "/health/live", headers={"X-Request-ID": "test-request-id"}
    )

    assert response.headers["X-Request-ID"] == "test-request-id"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Permissions-Policy"] == (
        "camera=(), microphone=(), geolocation=()"
    )


async def test_middleware_generates_request_id_for_invalid_input() -> None:
    response = await request(
        "/health/live", headers={"X-Request-ID": "invalid request id"}
    )

    assert UUID(response.headers["X-Request-ID"])


async def test_not_found_uses_error_envelope() -> None:
    response = await request("/missing", headers={"X-Request-ID": "missing-route"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Not Found",
            "request_id": "missing-route",
        }
    }


async def test_validation_error_uses_error_envelope() -> None:
    app = create_test_app()

    @app.get("/validate")
    async def validate(value: int) -> dict[str, int]:
        return {"value": value}

    response = await request(
        "/validate?value=invalid",
        app=app,
        headers={"X-Request-ID": "validation-request"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
            "request_id": "validation-request",
        }
    }


async def test_unexpected_error_is_safe_and_includes_request_id() -> None:
    app = create_test_app()

    @app.get("/explode")
    async def explode() -> None:
        raise RuntimeError("sensitive internal details")

    response = await request(
        "/explode",
        app=app,
        headers={"X-Request-ID": "failed-request"},
        raise_app_exceptions=False,
    )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "failed-request"
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Internal server error",
            "request_id": "failed-request",
        }
    }
    assert "sensitive" not in response.text


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "PUT", "DELETE"])
async def test_cors_allows_stage_six_methods_for_configured_origin(
    method: str,
) -> None:
    app = create_test_app(cors_origins="https://app.example.com")
    response = await request(
        "/health/live",
        app=app,
        method="OPTIONS",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": method,
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == (
        "https://app.example.com"
    )
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert method in response.headers["Access-Control-Allow-Methods"]


async def test_cors_rejects_unconfigured_origin() -> None:
    app = create_test_app(cors_origins="https://app.example.com")
    response = await request(
        "/health/live",
        app=app,
        method="OPTIONS",
        headers={
            "Origin": "https://attacker.example.com",
            "Access-Control-Request-Method": "PUT",
        },
    )

    assert response.status_code == 400
    assert "Access-Control-Allow-Origin" not in response.headers


def test_openapi_follows_debug_setting() -> None:
    disabled_app = create_test_app(app_debug=False)
    enabled_app = create_test_app(app_debug=True)

    assert disabled_app.openapi_url is None
    assert enabled_app.openapi_url == "/openapi.json"
