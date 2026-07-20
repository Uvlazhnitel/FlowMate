from typing import cast

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from flowmate.api.app import create_app
from flowmate.api.dependencies import get_engine
from flowmate.core.config import Settings
from tests.conftest import started_app


class FakeConnection:
    async def execute(self, statement: object) -> None:
        del statement


class FakeConnectionContext:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def __aenter__(self) -> FakeConnection:
        if self.fail:
            raise SQLAlchemyError("database unavailable")
        return FakeConnection()

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback


class FakeEngine:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext(fail=self.fail)


def create_health_app(engine: FakeEngine) -> FastAPI:
    app = create_app(settings=Settings(_env_file=None, app_api_key="test-secret"))

    def override_engine() -> AsyncEngine:
        return cast(AsyncEngine, engine)

    app.dependency_overrides[get_engine] = override_engine
    return app


async def get(app: FastAPI, path: str) -> tuple[int, dict[str, str]]:
    async with started_app(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(path)
    return response.status_code, response.json()


async def test_liveness_does_not_resolve_database_dependency() -> None:
    app = create_health_app(FakeEngine())

    def fail_if_resolved() -> AsyncEngine:
        raise AssertionError("liveness must not access the database")

    app.dependency_overrides[get_engine] = fail_if_resolved
    status_code, body = await get(app, "/health/live")

    assert status_code == 200
    assert body == {"status": "ok"}


async def test_readiness_reports_healthy_database() -> None:
    status_code, body = await get(create_health_app(FakeEngine()), "/health/ready")

    assert status_code == 200
    assert body == {"status": "ok"}


async def test_readiness_reports_failed_database_without_details() -> None:
    status_code, body = await get(
        create_health_app(FakeEngine(fail=True)), "/health/ready"
    )

    assert status_code == 503
    assert body == {"status": "unavailable"}
