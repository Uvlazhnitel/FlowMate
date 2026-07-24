from fastapi import APIRouter

from flowmate.api.routes.auth import router as auth_router
from flowmate.api.routes.health import router as health_router
from flowmate.api.routes.me import router as me_router
from flowmate.api.routes.meetings import router as meetings_router
from flowmate.api.routes.operations import router as operations_router
from flowmate.api.routes.remaining import router as remaining_router
from flowmate.api.routes.status import router as status_router
from flowmate.api.routes.workspace import router as workspace_router


def create_router() -> APIRouter:
    router = APIRouter()
    router.include_router(auth_router)
    router.include_router(health_router)
    router.include_router(me_router)
    router.include_router(meetings_router)
    router.include_router(operations_router)
    router.include_router(remaining_router)
    router.include_router(status_router)
    router.include_router(workspace_router)
    return router
