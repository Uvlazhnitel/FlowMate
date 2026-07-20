from fastapi import APIRouter, Depends

from flowmate.api.schemas import ErrorResponse, MeResponse
from flowmate.auth.bearer import require_bearer_token

router = APIRouter(prefix="/api/v1", tags=["session"])


@router.get(
    "/me",
    dependencies=[Depends(require_bearer_token)],
    response_model=MeResponse,
    responses={401: {"model": ErrorResponse}},
)
async def current_session() -> MeResponse:
    return MeResponse()
