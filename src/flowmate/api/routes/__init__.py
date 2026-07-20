from fastapi import APIRouter

from flowmate.api.routes.health import router as health_router
from flowmate.api.routes.status import router as status_router


def create_router() -> APIRouter:
    router = APIRouter()
    router.include_router(health_router)
    router.include_router(status_router)
    return router
