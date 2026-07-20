from httpx import ASGITransport, AsyncClient, Response

from flowmate.api.app import create_app
from flowmate.core.config import Settings
from tests.conftest import started_app


async def request(
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    settings = Settings(_env_file=None, app_api_key="test-secret")
    app = create_app(settings=settings)
    async with started_app(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers=headers)


async def test_status_rejects_missing_token() -> None:
    response = await request("/api/v1/status")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_status_rejects_invalid_token() -> None:
    response = await request(
        "/api/v1/status", headers={"Authorization": "Bearer wrong-secret"}
    )

    assert response.status_code == 401


async def test_status_accepts_valid_token() -> None:
    response = await request(
        "/api/v1/status", headers={"Authorization": "Bearer test-secret"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "flowmate"}


async def test_middleware_adds_request_id() -> None:
    response = await request(
        "/health/live", headers={"X-Request-ID": "test-request-id"}
    )

    assert response.headers["X-Request-ID"] == "test-request-id"


def test_openapi_follows_debug_setting() -> None:
    disabled_app = create_app(
        settings=Settings(_env_file=None, app_api_key="secret", app_debug=False)
    )
    enabled_app = create_app(
        settings=Settings(_env_file=None, app_api_key="secret", app_debug=True)
    )

    assert disabled_app.openapi_url is None
    assert enabled_app.openapi_url == "/openapi.json"
