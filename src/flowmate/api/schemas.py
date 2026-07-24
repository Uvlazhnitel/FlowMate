from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]


class StatusResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["flowmate"] = "flowmate"


class MeResponse(BaseModel):
    id: Literal["single-user"] = "single-user"
    authentication: Literal["api-key"] = "api-key"


class LoginCodeResponse(BaseModel):
    status: Literal["code_sent"] = "code_sent"
    expires_in_seconds: int


class LoginCodeVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: str = Field(pattern=r"^\d{6}$")


class PwaUserResponse(BaseModel):
    id: UUID
    display_name: str | None
    timezone: str
    default_snooze_minutes: int
    date_display_format: Literal["day_month_year", "year_month_day"]
    time_display_format: Literal["24h", "12h"]
    active_workspace: Literal["personal", "work"]


class ErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
