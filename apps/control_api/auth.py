"""Bearer / X-Control-Token auth for control API."""

from __future__ import annotations

from fastapi import Header, HTTPException, status

from apps.control_api.settings import get_settings


def require_token(
    authorization: str | None = Header(default=None),
    x_control_token: str | None = Header(default=None, alias="X-Control-Token"),
) -> None:
    settings = get_settings()
    if not settings.token:
        return
    presented: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()
    elif x_control_token:
        presented = x_control_token.strip()
    if not presented or presented != settings.token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing token",
        )
