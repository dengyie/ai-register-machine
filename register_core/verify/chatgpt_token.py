"""ChatGPT register result verifier — token shape gate (no live spend by default)."""

from __future__ import annotations

from typing import Any

from register_core.contracts import RegisterResult, VerifyResult


class ChatGPTTokenVerifier:
    """Validate refresh/access token presence and JWT-ish shape offline."""

    name = "chatgpt_token"

    def __init__(self, *, live: bool = False, **_: Any) -> None:
        self.live = live

    def verify(self, result: RegisterResult) -> VerifyResult:
        if not result.ok:
            return VerifyResult(
                ok=False,
                provider="chatgpt",
                capability="oauth_token",
                detail="register not ok",
            )
        secret = (result.secret or "").strip()
        kind = (result.secret_kind or "").strip().lower()
        if kind and kind not in ("refresh_token", "access_token", "token"):
            return VerifyResult(
                ok=False,
                provider="chatgpt",
                capability="oauth_token",
                detail=f"unexpected secret_kind={kind}",
            )
        if len(secret) < 20:
            return VerifyResult(
                ok=False,
                provider="chatgpt",
                capability="oauth_token",
                detail=f"token too short len={len(secret)}",
            )
        # OpenAI platform refresh tokens are opaque. Modern shape is rt.1.<payload>
        # (exactly two dots, short first segment) — must NOT be treated as a JWT.
        # Access tokens are JWTs whose base64url header typically starts with eyJ.
        if secret.startswith(("rt.", "rt_")):
            pass
        elif secret.count(".") == 2 and len(secret.split(".", 1)[0]) < 10:
            return VerifyResult(
                ok=False,
                provider="chatgpt",
                capability="oauth_token",
                detail="token looks malformed JWT header",
            )
        if not self.live:
            return VerifyResult(
                ok=True,
                provider="chatgpt",
                capability="oauth_token",
                detail="shape_ok (live probe off)",
                meta={"secret_len": len(secret), "secret_kind": kind or "refresh_token"},
            )
        # Live OpenAI API probe left Manual-required (cost / ToS).
        return VerifyResult(
            ok=False,
            provider="chatgpt",
            capability="oauth_token",
            detail="live probe not enabled in this milestone",
        )
