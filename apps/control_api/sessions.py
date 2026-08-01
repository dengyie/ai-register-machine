"""Signed HttpOnly session cookies for browser login."""

from __future__ import annotations

import time
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from apps.control_api.settings import MAX_SESSION_TTL_SECONDS

COOKIE_NAME = "control_session"


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="control-api-session-v1")


def mint_session_token(*, secret: str, username: str, ttl_seconds: int) -> str:
    if not secret:
        raise ValueError("session secret required")
    # Bake the ttl into the payload, clamped to the hard ceiling so a token
    # whose ttl was somehow raised past the cap (older build, or a caller that
    # bypassed get_settings) still verifies within the bound at read time.
    ttl = max(0, min(int(ttl_seconds), MAX_SESSION_TTL_SECONDS))
    payload = {
        "u": username,
        "iat": int(time.time()),
        "ttl": ttl,
    }
    return _serializer(secret).dumps(payload)


def read_session_token(
    token: str,
    *,
    secret: str,
    max_age: int,
    max_ttl: int = MAX_SESSION_TTL_SECONDS,
) -> dict[str, Any] | None:
    """Return the decoded session payload, or None for any invalid/expired token.

    Two independent guards bound a token's lifetime:

    - ``max_age``: itsdangerous rejects any token whose ``iat`` is older than
      ``max_age`` seconds (the server-side ceiling, normally
      settings.session_ttl_seconds).
    - ``max_ttl``: the payload's baked-in ``ttl`` field is re-checked against
      this hard ceiling (MAX_SESSION_TTL_SECONDS). A token minted by an older
      build that did NOT clamp the ttl, or whose ttl was raised past the cap,
      is refused even if it is still within ``max_age`` — so the lifetime can
      only ever shrink, never grow past the constant.
    """
    if not token or not secret:
        return None
    try:
        payload = _serializer(secret).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    user = payload.get("u")
    if not isinstance(user, str) or not user:
        return None
    # Re-validate the baked-in ttl against the hard ceiling. A token carrying a
    # ttl above max_ttl is untrustworthy (older build / tampered) — refuse it.
    ttl = payload.get("ttl")
    if not isinstance(ttl, int) or ttl > max_ttl or ttl < 0:
        return None
    return payload


def session_cookie_kwargs(*, secure: bool, max_age: int) -> dict[str, Any]:
    # Cookie max_age is also clamped to the hard ceiling so a runaway TTL env
    # can't ask the browser to keep an effectively-immortal cookie either.
    capped = max(0, min(int(max_age), MAX_SESSION_TTL_SECONDS))
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "strict",
        "secure": secure,
        "path": "/",
        "max_age": capped,
    }
