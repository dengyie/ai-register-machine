"""typesafe.ai register result verifier — API key shape gate (no live spend)."""

from __future__ import annotations

from typing import Any

from register_core.contracts import RegisterResult, VerifyResult


class TypesafeKeyVerifier:
    """Validate this-run API key presence and minimum length offline."""

    name = "typesafe_key"

    def __init__(self, *, live: bool = False, **_: Any) -> None:
        self.live = live

    def verify(self, result: RegisterResult) -> VerifyResult:
        if not result.ok:
            return VerifyResult(
                ok=False,
                provider="typesafe",
                capability="api_key",
                detail="register not ok",
            )
        secret = (result.secret or "").strip()
        kind = (result.secret_kind or "").strip().lower()
        if kind and kind not in ("api_key", "token"):
            return VerifyResult(
                ok=False,
                provider="typesafe",
                capability="api_key",
                detail=f"unexpected secret_kind={kind}",
            )
        if len(secret) < 16:
            return VerifyResult(
                ok=False,
                provider="typesafe",
                capability="api_key",
                detail=f"api_key too short len={len(secret)}",
            )
        return VerifyResult(
            ok=True,
            provider="typesafe",
            capability="api_key",
            detail="shape_ok (live spend not enabled)" if self.live else "shape_ok (live probe off)",
            meta={
                "secret_len": len(secret),
                "secret_kind": kind or "api_key",
                "live": bool(self.live),
            },
        )
