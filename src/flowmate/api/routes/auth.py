from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.api.dependencies import get_session
from flowmate.api.schemas import (
    ErrorResponse,
    LoginCodeResponse,
    LoginCodeVerifyRequest,
    PwaUserResponse,
)
from flowmate.auth.delivery import LoginCodeDeliveryError, LoginCodeSender
from flowmate.auth.dependencies import (
    PwaIdentity,
    require_csrf,
    require_pwa_origin,
    require_pwa_session,
)
from flowmate.auth.pwa import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    InvalidLoginCodeError,
    LoginCodeRateLimitedError,
    PwaAuthConfigurationError,
    issue_login_code,
    revoke_session,
    verify_login_code,
)
from flowmate.core.config import Settings, get_settings
from flowmate.reminders.preferences import (
    NotificationDefaults,
    get_effective_notification_preferences,
)

router = APIRouter(prefix="/api/v1/auth", tags=["pwa-auth"])


def _sender(request: Request) -> LoginCodeSender:
    sender: LoginCodeSender | None = request.app.state.login_code_sender
    if sender is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PWA authentication is not configured",
        )
    return sender


async def _user_response(
    identity: PwaIdentity,
    session: AsyncSession,
    settings: Settings,
) -> PwaUserResponse:
    preferences = await get_effective_notification_preferences(
        session,
        identity.user.id,
        NotificationDefaults.from_settings(settings),
    )
    return PwaUserResponse(
        id=identity.user.id,
        display_name=identity.user.display_name,
        timezone=preferences.timezone,
        default_snooze_minutes=preferences.default_snooze_minutes,
    )


def _set_auth_cookies(
    response: Response,
    settings: Settings,
    token: str,
    csrf_token: str,
) -> None:
    max_age = settings.pwa_session_ttl_days * 86_400
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=max_age,
        secure=settings.pwa_cookie_secure,
        samesite="lax",
        path="/",
        httponly=True,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=max_age,
        secure=settings.pwa_cookie_secure,
        samesite="lax",
        path="/",
        httponly=False,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=settings.pwa_cookie_secure,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        path="/",
        secure=settings.pwa_cookie_secure,
        httponly=False,
        samesite="lax",
    )


@router.post(
    "/login-code",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=LoginCodeResponse,
    responses={429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    dependencies=[Depends(require_pwa_origin)],
)
async def request_login_code(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LoginCodeResponse:
    try:
        await issue_login_code(session, settings, _sender(request))
    except PwaAuthConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PWA authentication is not configured",
        ) from error
    except LoginCodeRateLimitedError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login code requests",
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from error
    except LoginCodeDeliveryError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login code could not be delivered",
        ) from error
    return LoginCodeResponse(expires_in_seconds=settings.pwa_login_code_ttl_seconds)


@router.post(
    "/session",
    response_model=PwaUserResponse,
    responses={401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    dependencies=[Depends(require_pwa_origin)],
)
async def create_pwa_session(
    payload: LoginCodeVerifyRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PwaUserResponse:
    try:
        created = await verify_login_code(session, settings, payload.code)
    except InvalidLoginCodeError as error:
        if error.attempt_recorded:
            await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Login code is invalid or expired",
        ) from error
    except PwaAuthConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PWA authentication is not configured",
        ) from error
    _set_auth_cookies(
        response,
        settings,
        created.token,
        created.csrf_token,
    )
    preferences = await get_effective_notification_preferences(
        session,
        created.user.id,
        NotificationDefaults.from_settings(settings),
    )
    return PwaUserResponse(
        id=created.user.id,
        display_name=created.user.display_name,
        timezone=preferences.timezone,
        default_snooze_minutes=preferences.default_snooze_minutes,
    )


@router.get(
    "/me",
    response_model=PwaUserResponse,
    responses={401: {"model": ErrorResponse}},
)
async def current_pwa_user(
    identity: Annotated[PwaIdentity, Depends(require_pwa_session)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PwaUserResponse:
    return await _user_response(identity, session, settings)


@router.delete(
    "/session",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def logout_pwa_session(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    _: Annotated[PwaIdentity, Depends(require_csrf)],
) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    await revoke_session(session, token)
    clear_auth_cookies(response, settings)
