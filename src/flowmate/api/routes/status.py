from fastapi import APIRouter, Depends

from flowmate.auth.bearer import require_bearer_token

router = APIRouter(prefix="/api/v1", tags=["status"])


@router.get("/status", dependencies=[Depends(require_bearer_token)])
async def application_status() -> dict[str, str]:
    return {"status": "ok", "service": "flowmate"}
