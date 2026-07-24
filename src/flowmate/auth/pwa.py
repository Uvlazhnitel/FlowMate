from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from flowmate.auth.delivery import LoginCodeSender
from flowmate.core.config import Settings
from flowmate.db.models import PwaLoginCode, PwaSession, User
from flowmate.db.users import get_or_create_telegram_user

SESSION_COOKIE_NAME = "flowmate_session"
CSRF_COOKIE_NAME = "flowmate_csrf"


class PwaAuthError(Exception):
    """Base error for expected PWA authentication failures."""


class PwaAuthConfigurationError(PwaAuthError):
    pass


class LoginCodeRateLimitedError(PwaAuthError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("login code request rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class InvalidLoginCodeError(PwaAuthError):
    def __init__(self, *, attempt_recorded: bool = False) -> None:
        super().__init__("login code is invalid")
        self.attempt_recorded = attempt_recorded


class InvalidPwaSessionError(PwaAuthError):
    pass


@dataclass(frozen=True, slots=True)
class IssuedLoginCode:
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CreatedPwaSession:
    session: PwaSession
    user: User
    token: str
    csrf_token: str


def auth_now() -> datetime:
    return datetime.now(UTC)


def generate_login_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def generate_secret_token() -> str:
    return secrets.token_urlsafe(32)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _code_digest(secret: str, code_id: UUID, code: str) -> str:
    return hmac.new(
        secret.encode(),
        f"{code_id}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _configured_settings(settings: Settings) -> Settings:
    try:
        return settings.require_pwa_auth()
    except ValueError as error:
        raise PwaAuthConfigurationError from error


async def _lock_owner(session: AsyncSession, settings: Settings) -> User:
    configured = _configured_settings(settings)
    telegram_user_id = configured.pwa_telegram_user_id
    assert telegram_user_id is not None
    user, _ = await get_or_create_telegram_user(
        session,
        telegram_user_id,
        active_workspace=configured.app_active_workspace,
    )
    locked = await session.scalar(
        select(User).where(User.id == user.id).with_for_update()
    )
    if locked is None or not locked.is_active:
        raise PwaAuthConfigurationError
    return locked


async def issue_login_code(
    session: AsyncSession,
    settings: Settings,
    sender: LoginCodeSender,
    *,
    now: datetime | None = None,
    code: str | None = None,
) -> IssuedLoginCode:
    configured = _configured_settings(settings)
    current = now or auth_now()
    user = await _lock_owner(session, configured)
    window_start = current - timedelta(
        seconds=configured.pwa_login_request_window_seconds
    )
    request_times = list(
        await session.scalars(
            select(PwaLoginCode.created_at)
            .where(
                PwaLoginCode.user_id == user.id,
                PwaLoginCode.created_at >= window_start,
            )
            .order_by(PwaLoginCode.created_at)
        )
    )
    if len(request_times) >= configured.pwa_login_request_limit:
        retry_at = request_times[0] + timedelta(
            seconds=configured.pwa_login_request_window_seconds
        )
        retry_after = max(1, int((retry_at - current).total_seconds()))
        raise LoginCodeRateLimitedError(retry_after)

    await session.execute(
        update(PwaLoginCode)
        .where(
            PwaLoginCode.user_id == user.id,
            PwaLoginCode.used_at.is_(None),
            PwaLoginCode.invalidated_at.is_(None),
        )
        .values(invalidated_at=current)
    )
    raw_code = code or generate_login_code()
    if len(raw_code) != 6 or not raw_code.isdigit():
        raise ValueError("login code must contain exactly six digits")
    code_id = uuid4()
    expires_at = current + timedelta(seconds=configured.pwa_login_code_ttl_seconds)
    secret = configured.pwa_auth_secret
    telegram_user_id = configured.pwa_telegram_user_id
    assert secret is not None and telegram_user_id is not None
    login_code = PwaLoginCode(
        id=code_id,
        user_id=user.id,
        code_digest=_code_digest(secret.get_secret_value(), code_id, raw_code),
        expires_at=expires_at,
        created_at=current,
    )
    session.add(login_code)
    await session.flush()
    await sender.send_login_code(
        telegram_user_id,
        raw_code,
        max(1, configured.pwa_login_code_ttl_seconds // 60),
    )
    return IssuedLoginCode(expires_at=expires_at)


async def verify_login_code(
    session: AsyncSession,
    settings: Settings,
    code: str,
    *,
    now: datetime | None = None,
    token: str | None = None,
    csrf_token: str | None = None,
) -> CreatedPwaSession:
    configured = _configured_settings(settings)
    current = now or auth_now()
    user = await _lock_owner(session, configured)
    login_code = await session.scalar(
        select(PwaLoginCode)
        .where(
            PwaLoginCode.user_id == user.id,
            PwaLoginCode.used_at.is_(None),
            PwaLoginCode.invalidated_at.is_(None),
        )
        .order_by(PwaLoginCode.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if (
        login_code is None
        or login_code.expires_at <= current
        or login_code.attempt_count >= configured.pwa_login_max_attempts
    ):
        if login_code is not None:
            login_code.invalidated_at = current
            await session.flush()
        raise InvalidLoginCodeError(attempt_recorded=login_code is not None)

    secret = configured.pwa_auth_secret
    assert secret is not None
    supplied_digest = _code_digest(secret.get_secret_value(), login_code.id, code)
    if not hmac.compare_digest(login_code.code_digest, supplied_digest):
        login_code.attempt_count += 1
        if login_code.attempt_count >= configured.pwa_login_max_attempts:
            login_code.invalidated_at = current
        await session.flush()
        raise InvalidLoginCodeError(attempt_recorded=True)

    login_code.used_at = current
    active_sessions = list(
        await session.scalars(
            select(PwaSession)
            .where(
                PwaSession.user_id == user.id,
                PwaSession.revoked_at.is_(None),
                PwaSession.expires_at > current,
            )
            .order_by(PwaSession.created_at, PwaSession.id)
            .with_for_update()
        )
    )
    revoke_count = max(
        0,
        len(active_sessions) - configured.pwa_max_active_sessions + 1,
    )
    for old_session in active_sessions[:revoke_count]:
        old_session.revoked_at = current

    raw_token = token or generate_secret_token()
    raw_csrf = csrf_token or generate_secret_token()
    pwa_session = PwaSession(
        user_id=user.id,
        token_digest=_sha256(raw_token),
        csrf_digest=_sha256(raw_csrf),
        expires_at=current + timedelta(days=configured.pwa_session_ttl_days),
        created_at=current,
    )
    session.add(pwa_session)
    await session.flush()
    return CreatedPwaSession(
        session=pwa_session,
        user=user,
        token=raw_token,
        csrf_token=raw_csrf,
    )


async def get_authenticated_session(
    session: AsyncSession,
    token: str | None,
    *,
    now: datetime | None = None,
    for_update: bool = False,
) -> tuple[PwaSession, User]:
    if not token:
        raise InvalidPwaSessionError
    current = now or auth_now()
    statement = (
        select(PwaSession, User)
        .join(User, User.id == PwaSession.user_id)
        .where(
            PwaSession.token_digest == _sha256(token),
            PwaSession.revoked_at.is_(None),
            PwaSession.expires_at > current,
            User.is_active.is_(True),
        )
    )
    if for_update:
        statement = statement.with_for_update(of=PwaSession)
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise InvalidPwaSessionError
    return row[0], row[1]


def csrf_is_valid(pwa_session: PwaSession, cookie: str, header: str) -> bool:
    return hmac.compare_digest(cookie, header) and hmac.compare_digest(
        pwa_session.csrf_digest,
        _sha256(header),
    )


async def revoke_session(
    session: AsyncSession,
    token: str,
    *,
    now: datetime | None = None,
) -> None:
    pwa_session, _ = await get_authenticated_session(
        session,
        token,
        now=now,
        for_update=True,
    )
    pwa_session.revoked_at = now or auth_now()
    await session.flush()
