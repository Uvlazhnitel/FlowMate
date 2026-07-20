from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.cors import CORSMiddleware

from flowmate.api.errors import register_exception_handlers
from flowmate.api.middleware import RequestContextMiddleware
from flowmate.api.routes import create_router
from flowmate.core.config import Settings, get_settings
from flowmate.core.logging import configure_logging
from flowmate.db.session import create_engine, create_session_factory


def create_app(
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
) -> FastAPI:
    app_settings = (settings or get_settings()).require_api()
    configure_logging(
        app_settings.log_level,
        structured=app_settings.app_env == "production",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = engine or create_engine(app_settings.database_url)
        app.state.session_factory = create_session_factory(app.state.engine)
        logging.getLogger(__name__).info("application_started")
        try:
            yield
        finally:
            await app.state.engine.dispose()
            logging.getLogger(__name__).info("application_stopped")

    docs_url = "/docs" if app_settings.app_debug else None
    openapi_url = "/openapi.json" if app_settings.app_debug else None
    app = FastAPI(
        title="FlowMate API",
        version="0.1.0",
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    if settings is not None:

        def get_injected_settings() -> Settings:
            return app_settings

        app.dependency_overrides[get_settings] = get_injected_settings
    if app_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=sorted(app_settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(create_router())
    return app
