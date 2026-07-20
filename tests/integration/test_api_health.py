import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from flowmate.api.app import create_app
from flowmate.config import Settings
from tests.conftest import started_app


@pytest.mark.integration
async def test_health_endpoints(database_engine: AsyncEngine) -> None:
    settings = Settings(_env_file=None, api_bearer_token="test-secret")
    app = create_app(settings=settings, engine=database_engine)

    async with started_app(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            live_response = await client.get("/health/live")
            ready_response = await client.get("/health/ready")

    assert live_response.status_code == 200
    assert live_response.json() == {"status": "ok"}
    assert ready_response.status_code == 200
    assert ready_response.json() == {"status": "ok"}
