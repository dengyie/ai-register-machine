"""Outlook HOLD captcha bridge via slidex CDP with storage-state fallback.

Preferred path attaches slidex to the same browser over CDP. On known
coordinate/frame attach failures, exports storage_state into a temporary
context for PLAYWRIGHT_PAGE solve, then closes only that temp context.

Security: never put CDP endpoint, cookies, tokens, page content, storage_state,
or proxy URLs into CaptchaBridgeResult.metadata or logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from slidex.vision import (
    ChallengeType,
    VisionContext,
    VisualChallengeRequest,
    VisualChallengeSolver,
)

# Only these solver error codes trigger the storage-state temporary-context path.
_FALLBACK_ERROR_CODES = frozenset(
    {
        "captcha_frame_missing",
        "unsupported_hold_context",
    }
)

# Keys that must never appear in bridge result metadata (defense in depth).
_SECRET_METADATA_KEYS = frozenset(
    {
        "cdp_endpoint",
        "endpoint",
        "ws_endpoint",
        "cookie",
        "cookies",
        "storage_state",
        "token",
        "tokens",
        "password",
        "proxy",
        "proxy_url",
        "page_content",
        "html",
        "body",
        "authorization",
        "refresh_token",
        "access_token",
    }
)


def _sanitize_metadata(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Return a shallow copy with secret-shaped keys stripped."""
    if not raw:
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        key_l = str(key).lower()
        if key_l in _SECRET_METADATA_KEYS:
            continue
        if any(part in key_l for part in ("token", "cookie", "password", "proxy", "endpoint")):
            continue
        # Values must also stay non-secret; drop nested dicts/lists wholesale.
        if isinstance(value, (dict, list, tuple, bytes, bytearray)):
            continue
        text = str(value).lower() if value is not None else ""
        if any(
            needle in text
            for needle in (
                "ws://",
                "wss://",
                "cookie",
                "token=",
                "bearer ",
                "proxy",
            )
        ):
            continue
        out[key] = value
    return out


@dataclass(frozen=True, slots=True)
class CaptchaBridgeResult:
    ok: bool
    error_kind: str = ""
    error: str = ""
    fallback_used: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


class OutlookCaptchaBridge:
    """Bridge Outlook HOLD challenges through slidex without restarting main context."""

    def __init__(self, *, solver=None, endpoint_getter=None) -> None:
        self.solver = solver or VisualChallengeSolver()
        self.endpoint_getter = endpoint_getter

    async def solve(
        self,
        page=None,
        *,
        browser,
        page_url: str,
        timeout_ms: int = 30_000,
    ) -> CaptchaBridgeResult:
        # Preferred same-browser CDP HOLD request.
        endpoint = await self._endpoint(browser)
        request = VisualChallengeRequest(
            challenge_type=ChallengeType.HOLD,
            context=VisionContext.CDP,
            cdp_endpoint=endpoint,
            page_url=page_url,
            timeout_ms=timeout_ms,
            metadata={"site": "outlook"},
        )
        result = await self.solver.solve(request)
        if result.success:
            return CaptchaBridgeResult(
                True,
                metadata=_sanitize_metadata({"path": "cdp"}),
            )

        error_code = getattr(result, "error_code", None) or ""
        if error_code in _FALLBACK_ERROR_CODES:
            return await self._solve_storage_state(page, browser, page_url, timeout_ms)

        retryable = bool(getattr(result, "retryable", False))
        error_kind = "proxy" if error_code == "ip_blocked" else "captcha"
        return CaptchaBridgeResult(
            False,
            error_kind=error_kind,
            error=error_code or "captcha_failed",
            metadata=_sanitize_metadata({"path": "cdp", "retryable": retryable}),
        )

    async def _endpoint(self, browser) -> str:
        """Return CDP endpoint for the solver only; never log it."""
        if self.endpoint_getter:
            value = self.endpoint_getter(browser)
            if hasattr(value, "__await__"):
                value = await value
            return str(value or "")
        endpoint = getattr(browser, "ws_endpoint", None)
        if callable(endpoint):
            value = endpoint()
            if hasattr(value, "__await__"):
                value = await value
            return str(value or "")
        return str(endpoint or "")

    async def _solve_storage_state(
        self,
        page,
        browser,
        page_url: str,
        timeout_ms: int,
    ) -> CaptchaBridgeResult:
        """Temporary second context with storage_state; main context stays intact."""
        if page is None or not hasattr(page, "context"):
            return CaptchaBridgeResult(
                False,
                error_kind="captcha",
                error="storage_state_unavailable",
                fallback_used=True,
                metadata=_sanitize_metadata({"path": "storage_state", "retryable": False}),
            )

        # Cookies may exist in memory here only; never place into result metadata.
        state = await page.context.storage_state()
        context = await browser.new_context(storage_state=state)
        try:
            fallback_page = await context.new_page()
            await fallback_page.goto(page_url)
            request = VisualChallengeRequest(
                challenge_type=ChallengeType.HOLD,
                context=VisionContext.PLAYWRIGHT_PAGE,
                page=fallback_page,
                page_url=page_url,
                timeout_ms=timeout_ms,
                # Request metadata is solver-internal; result metadata is sanitized.
                metadata={"site": "outlook", "path": "storage_state"},
            )
            result = await self.solver.solve(request)
            ok = bool(result.success)
            error_code = getattr(result, "error_code", None) or ""
            retryable = bool(getattr(result, "retryable", False))
            return CaptchaBridgeResult(
                ok,
                error_kind="" if ok else "captcha",
                error="" if ok else (error_code or "captcha_failed"),
                fallback_used=True,
                metadata=_sanitize_metadata(
                    {"path": "storage_state", "retryable": retryable}
                ),
            )
        finally:
            # Close only the temporary context — never the main registration context.
            await context.close()
