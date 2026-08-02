"""Unit tests for Outlook HOLD captcha bridge (Task 6).

Default suite is browser-free: solvers, pages, and contexts are fakes only.
Uses asyncio.run (repo convention; pytest-asyncio is not a project dependency).
"""

from __future__ import annotations

import asyncio

from register_core.providers.outlook_captcha import OutlookCaptchaBridge


class FakeResult:
    def __init__(self, success, error_code=None, retryable=False):
        self.success = success
        self.error_code = error_code
        self.retryable = retryable


class _FakePage:
    def __init__(self, *, cookies=None):
        self.context = _FakeBrowserContext(cookies=cookies)
        self.goto_urls: list[str] = []

    async def goto(self, url: str):
        self.goto_urls.append(url)


class _FakeBrowserContext:
    def __init__(self, *, cookies=None):
        self._cookies = list(cookies or [])
        self.closed = False
        self.pages: list[_FakePage] = []

    async def storage_state(self):
        # Deliberately secret-shaped; must never appear in bridge metadata.
        return {
            "cookies": self._cookies
            or [{"name": "RPS", "value": "secret-token-value", "domain": ".live.com"}],
            "origins": [],
        }

    async def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page

    async def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, *, ws_endpoint: str = "ws://synthetic-cdp"):
        self.ws_endpoint = ws_endpoint
        self.contexts: list[_FakeBrowserContext] = []

    async def new_context(self, **kwargs):
        # storage_state may be passed; bridge must not log or return it.
        ctx = _FakeBrowserContext(cookies=(kwargs.get("storage_state") or {}).get("cookies"))
        self.contexts.append(ctx)
        return ctx


def _solver(handler):
    return type("S", (), {"solve": handler})()


def test_bridge_constructs_hold_cdp_request():
    seen = {}

    async def solve(self, request):
        seen["request"] = request
        return FakeResult(True)

    async def _run():
        browser = type("B", (), {"ws_endpoint": "ws://synthetic-cdp"})()
        bridge = OutlookCaptchaBridge(solver=_solver(solve))
        return await bridge.solve(
            page=object(), browser=browser, page_url="https://x.invalid"
        )

    result = asyncio.run(_run())
    assert result.ok is True
    assert seen["request"].challenge_type.value == "hold"
    assert seen["request"].context.value == "cdp"
    assert seen["request"].cdp_endpoint == "ws://synthetic-cdp"
    assert seen["request"].page_url == "https://x.invalid"
    assert result.fallback_used is False
    assert result.metadata.get("path") == "cdp"
    # Endpoint must reach the solver only — never result metadata.
    blob = str(result.metadata).lower()
    assert "ws://" not in blob
    assert "synthetic-cdp" not in blob
    assert "endpoint" not in blob
    assert "token" not in blob
    assert "cookie" not in blob


def test_bridge_reports_retryable_failure_without_secrets():
    async def solve(self, request):
        return FakeResult(False, "captcha_retry", retryable=True)

    async def _run():
        browser = type("B", (), {"ws_endpoint": "ws://synthetic-cdp"})()
        bridge = OutlookCaptchaBridge(solver=_solver(solve))
        return await bridge.solve(
            page=object(), browser=browser, page_url="https://x.invalid"
        )

    result = asyncio.run(_run())
    assert result.ok is False
    assert result.error_kind == "captcha"
    assert result.error == "captcha_retry"
    assert result.metadata.get("retryable") is True
    assert result.metadata.get("path") == "cdp"
    blob = str(result.metadata).lower()
    assert "token" not in blob
    assert "cookie" not in blob
    assert "ws://" not in blob
    assert "synthetic-cdp" not in blob
    assert "proxy" not in blob


def test_bridge_maps_ip_blocked_to_proxy_kind():
    async def solve(self, request):
        return FakeResult(False, "ip_blocked", retryable=True)

    async def _run():
        browser = type("B", (), {"ws_endpoint": "ws://synthetic-cdp"})()
        bridge = OutlookCaptchaBridge(solver=_solver(solve))
        return await bridge.solve(
            page=object(), browser=browser, page_url="https://x.invalid"
        )

    result = asyncio.run(_run())
    assert result.ok is False
    assert result.error_kind == "proxy"
    assert result.error == "ip_blocked"
    assert result.fallback_used is False


def test_bridge_falls_back_to_storage_state_without_restarting_main_context():
    calls: list = []

    async def solve(self, request):
        calls.append(request)
        ctx_val = getattr(request.context, "value", None) or str(request.context)
        if ctx_val == "cdp":
            return FakeResult(False, "captcha_frame_missing", retryable=True)
        return FakeResult(True)

    main_page = _FakePage(
        cookies=[{"name": "RPS", "value": "must-not-leak", "domain": ".live.com"}]
    )
    browser = _FakeBrowser(ws_endpoint="ws://synthetic-cdp-secret")

    async def _run():
        bridge = OutlookCaptchaBridge(solver=_solver(solve))
        return await bridge.solve(
            page=main_page,
            browser=browser,
            page_url="https://signup.live.com/signup",
            timeout_ms=12_000,
        )

    result = asyncio.run(_run())

    assert result.ok is True
    assert result.fallback_used is True
    assert result.metadata.get("path") == "storage_state"
    assert len(calls) == 2
    assert calls[0].context.value == "cdp"
    assert calls[1].context.value == "playwright_page"
    assert calls[1].page is not main_page
    assert browser.contexts, "fallback must open a temporary context"
    assert browser.contexts[0].closed is True, "temporary context must be closed"
    # Main page/context must remain open (never silently restarted).
    assert main_page.context.closed is False
    blob = str(result.metadata).lower()
    assert "must-not-leak" not in blob
    assert "rps" not in blob
    assert "cookie" not in blob
    assert "token" not in blob
    assert "ws://" not in blob
    assert "synthetic-cdp" not in blob


def test_bridge_fallback_on_unsupported_hold_context_and_closes_temp_context():
    async def solve(self, request):
        ctx = request.context.value
        if ctx == "cdp":
            return FakeResult(False, "unsupported_hold_context", retryable=False)
        return FakeResult(False, "captcha_retry", retryable=True)

    main_page = _FakePage()
    browser = _FakeBrowser()

    async def _run():
        bridge = OutlookCaptchaBridge(solver=_solver(solve))
        return await bridge.solve(
            page=main_page, browser=browser, page_url="https://x.invalid"
        )

    result = asyncio.run(_run())
    assert result.ok is False
    assert result.fallback_used is True
    assert result.error_kind == "captcha"
    assert result.error == "captcha_retry"
    assert result.metadata.get("path") == "storage_state"
    assert browser.contexts[0].closed is True
    assert main_page.context.closed is False


def test_bridge_uses_endpoint_getter_without_exposing_it():
    seen = {}

    async def solve(self, request):
        seen["endpoint"] = request.cdp_endpoint
        return FakeResult(True)

    async def getter(browser):
        return "ws://from-getter-only"

    async def _run():
        bridge = OutlookCaptchaBridge(
            solver=_solver(solve),
            endpoint_getter=getter,
        )
        return await bridge.solve(
            page=object(),
            browser=type("B", (), {"ws_endpoint": "ws://should-not-win"})(),
            page_url="https://x.invalid",
        )

    result = asyncio.run(_run())
    assert result.ok is True
    assert seen["endpoint"] == "ws://from-getter-only"
    assert "from-getter" not in str(result.metadata).lower()
    assert "endpoint" not in str(result.metadata).lower()


def test_adapter_captcha_bridge_seam_is_injectable(monkeypatch):
    """The bridge seam remains constructible/injectable under Task 9 orchestration."""
    from register_core.providers.outlook_adapter import OutlookProvider

    custom = OutlookCaptchaBridge()
    provider = OutlookProvider(config={"captcha_bridge": custom})
    assert provider._captcha_bridge() is custom
    # Default (no injection) returns a real bridge.
    assert isinstance(
        OutlookProvider(config={})._captcha_bridge(), OutlookCaptchaBridge
    )
