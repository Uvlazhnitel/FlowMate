from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine

from flowmate.core.config import Settings


def get_request_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine = request.app.state.engine
    return engine
