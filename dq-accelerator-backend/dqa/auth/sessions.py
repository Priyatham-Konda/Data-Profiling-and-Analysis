"""Session tokens: issue one at sign-in, resolve it per request, end it."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi.responses import Response

from .. import config
from ..store import registry


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start(user_id: str, response: Response) -> None:
    """Open a session for the user and set its cookie on the response."""
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=config.SESSION_DAYS)
    registry.create_session(_hash(token), user_id, expires)
    response.set_cookie(
        config.SESSION_COOKIE,
        token,
        max_age=config.SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        path="/",
    )


def user_for(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    return registry.session_user(_hash(token))


def end(token: Optional[str], response: Response) -> None:
    if token:
        registry.delete_session(_hash(token))
    response.delete_cookie(
        config.SESSION_COOKIE,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        path="/",
    )
