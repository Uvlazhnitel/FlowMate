from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncEngine

from flowmate.api.dependencies import get_engine
from flowmate.api.schemas import HealthResponse
from flowmate.db.health import database_is_ready

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
)
async def readiness(
    engine: Annotated[AsyncEngine, Depends(get_engine)], response: Response
) -> HealthResponse:
    if not await database_is_ready(engine):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="unavailable")
    return HealthResponse(status="ok")
