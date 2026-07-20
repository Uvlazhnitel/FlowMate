from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["flowmate"] = "flowmate"


class MeResponse(BaseModel):
    id: Literal["single-user"] = "single-user"
    authentication: Literal["api-key"] = "api-key"


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
