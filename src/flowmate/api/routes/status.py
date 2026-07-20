from fastapi import APIRouter, Depends

from flowmate.api.schemas import ErrorResponse, StatusResponse
from flowmate.auth.bearer import require_bearer_token

router = APIRouter(prefix="/api/v1", tags=["status"])


@router.get(
    "/status",
    dependencies=[Depends(require_bearer_token)],
    response_model=StatusResponse,
    responses={401: {"model": ErrorResponse}},
)
async def application_status() -> StatusResponse:
    return StatusResponse()
