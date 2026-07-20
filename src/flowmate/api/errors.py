import logging
from collections.abc import Mapping
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


def get_request_id(request: Request) -> str:
    request_id: str = getattr(request.state, "request_id", "unknown")
    return request_id


def error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": get_request_id(request),
            }
        },
        headers=headers,
    )


async def handle_http_exception(request: Request, error: Exception) -> JSONResponse:
    exception = cast(HTTPException, error)
    code = {
        401: "unauthorized",
        404: "not_found",
    }.get(exception.status_code, "http_error")
    message = (
        exception.detail if isinstance(exception.detail, str) else "Request failed"
    )
    return error_response(
        request,
        status_code=exception.status_code,
        code=code,
        message=message,
        headers=exception.headers,
    )


async def handle_validation_error(request: Request, error: Exception) -> JSONResponse:
    _ = cast(RequestValidationError, error)
    return error_response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed",
    )


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_request_error",
        exc_info=(type(error), error, error.__traceback__),
        extra={"request_id": get_request_id(request)},
    )
    return error_response(
        request,
        status_code=500,
        code="internal_error",
        message="Internal server error",
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
