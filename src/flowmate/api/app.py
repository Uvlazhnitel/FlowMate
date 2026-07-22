from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.middleware.cors import CORSMiddleware

from flowmate.ai.errors import AIConfigurationError
from flowmate.ai.factory import create_ai_provider
from flowmate.ai.provider import AIProvider, MeetingReviewProvider
from flowmate.api.errors import register_exception_handlers
from flowmate.api.middleware import RequestContextMiddleware
from flowmate.api.routes import create_router
from flowmate.auth.delivery import LoginCodeSender, TelegramLoginCodeSender
from flowmate.core.config import Settings, get_settings
from flowmate.core.logging import configure_logging
from flowmate.db.session import create_engine, create_session_factory


def create_app(
    settings: Settings | None = None,
    engine: AsyncEngine | None = None,
    login_code_sender: LoginCodeSender | None = None,
    meeting_review_provider: MeetingReviewProvider | None = None,
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
        login_bot: Bot | None = None
        owned_ai_provider: AIProvider | None = None
        app.state.login_code_sender = login_code_sender
        app.state.meeting_review_provider = meeting_review_provider
        if meeting_review_provider is None:
            try:
                owned_ai_provider = create_ai_provider(app_settings)
            except AIConfigurationError:
                logging.getLogger(__name__).warning(
                    "meeting_review_provider_disabled category=incomplete_configuration"
                )
            app.state.meeting_review_provider = owned_ai_provider
        if (
            login_code_sender is None
            and app_settings.pwa_telegram_user_id is not None
            and app_settings.pwa_auth_secret is not None
            and app_settings.telegram_bot_token is not None
        ):
            login_bot = Bot(app_settings.telegram_bot_token.get_secret_value())
            app.state.login_code_sender = TelegramLoginCodeSender(login_bot)
        logging.getLogger(__name__).info("application_started")
        try:
            yield
        finally:
            if login_bot is not None:
                await login_bot.session.close()
            if owned_ai_provider is not None:
                await owned_ai_provider.close()
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
    app.state.settings = app_settings
    if settings is not None:

        def get_injected_settings() -> Settings:
            return app_settings

        app.dependency_overrides[get_settings] = get_injected_settings
    if app_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=sorted(app_settings.cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-CSRF-Token",
                "X-Request-ID",
            ],
        )
    app.add_middleware(RequestContextMiddleware)
    register_exception_handlers(app)
    app.include_router(create_router())
    return app
