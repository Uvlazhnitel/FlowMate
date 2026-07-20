from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from flowmate.api.middleware import RequestContextMiddleware
from flowmate.api.routes import create_router
from flowmate.core.config import Settings, get_settings
from flowmate.core.logging import configure_logging
from flowmate.db.session import create_engine


def create_app(
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
) -> FastAPI:
    app_settings = (settings or get_settings()).require_api()
    configure_logging(app_settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = app_settings
        app.state.engine = engine or create_engine(app_settings.database_url)
        try:
            yield
        finally:
            await app.state.engine.dispose()

    docs_url = "/docs" if app_settings.api_docs_enabled else None
    openapi_url = "/openapi.json" if app_settings.api_docs_enabled else None
    app = FastAPI(
        title="FlowMate API",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(create_router())
    return app
