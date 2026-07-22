from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.api.dependencies import get_session
from flowmate.auth.pwa import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    InvalidPwaSessionError,
    csrf_is_valid,
    get_authenticated_session,
)
from flowmate.core.config import Settings, get_settings
from flowmate.db.models import PwaSession, User


class PwaSessionExpired(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is missing or expired",
        )


@dataclass(frozen=True, slots=True)
class PwaIdentity:
    session: PwaSession
    user: User


def require_pwa_origin(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    origin = request.headers.get("Origin")
    allowed = settings.cors_origins | {settings.pwa_public_origin}
    if origin not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is not allowed",
        )


async def require_pwa_session(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PwaIdentity:
    try:
        pwa_session, user = await get_authenticated_session(
            session,
            request.cookies.get(SESSION_COOKIE_NAME),
        )
    except InvalidPwaSessionError as error:
        raise PwaSessionExpired from error
    return PwaIdentity(session=pwa_session, user=user)


def require_csrf(
    request: Request,
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PwaIdentity:
    require_pwa_origin(request, settings)
    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    header = request.headers.get("X-CSRF-Token")
    if (
        cookie is None
        or header is None
        or not csrf_is_valid(
            identity.session,
            cookie,
            header,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed",
        )
    return identity
