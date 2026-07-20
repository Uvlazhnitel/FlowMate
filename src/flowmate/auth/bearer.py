from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from flowmate.api.dependencies import get_request_settings
from flowmate.core.config import Settings

bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_request_settings)],
) -> None:
    expected = settings.app_api_key
    if (
        credentials is None
        or credentials.scheme.lower() != "bearer"
        or expected is None
        or not secrets.compare_digest(credentials.credentials, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
