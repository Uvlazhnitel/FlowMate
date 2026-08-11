from fastapi import APIRouter

from .inbox import router as inbox_router
from .planning import router as planning_router
from .settings import router as settings_router

router = APIRouter(prefix="/api/v1", tags=["pwa-remaining"])
router.include_router(inbox_router)
router.include_router(planning_router)
router.include_router(settings_router)

__all__ = ["router"]
