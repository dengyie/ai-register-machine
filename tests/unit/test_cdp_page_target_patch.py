#!/usr/bin/env python3
"""Unit tests: CFT149 + turnstilePatch leaves only service_worker → create page.

DrissionPage 4.1.x test_connect requires type in {page, webview}. With
--load-extension=turnstilePatch, Chrome for Testing 149 often exposes only a
chrome-extension service_worker in /json, so Chromium() times out even though
DevTools is healthy. ensure_cdp_page_target + patch_drission_test_connect fix it.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tab_pool as tp  # noqa: E402


def test_cdp_target_has_page() -> None:
    assert tp.cdp_target_has_page(None) is False
    assert tp.cdp_target_has_page([]) is False
    assert tp.cdp_target_has_page([{"type": "service_worker"}]) is False
    assert tp.cdp_target_has_page([{"type": "page", "url": "about:blank"}]) is True
    assert tp.cdp_target_has_page(
        [
            {"type": "service_worker", "url": "chrome-extension://x/sw.js"},
            {"type": "webview", "url": "about:blank"},
        ]
    ) is True
    print("PASS  cdp_target_has_page")


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_ensure_cdp_page_target_creates_when_only_sw() -> None:
    """/json only service_worker → PUT /json/new → page appears."""
    state = {"n": 0}

    def fake_urlopen(req, timeout=2):  # noqa: ARG001
        url = getattr(req, "full_url", None) or getattr(req, "get_full_url", lambda: "")()
        method = getattr(req, "get_method", lambda: "GET")()
        state["n"] += 1
        if url.endswith("/json") and method == "GET":
            # first list: SW only; after PUT: page present
            if state.get("created"):
                body = json.dumps(
                    [
                        {"type": "page", "url": "about:blank"},
                        {"type": "service_worker", "url": "chrome-extension://x/sw.js"},
                    ]
                ).encode()
            else:
                body = json.dumps(
                    [{"type": "service_worker", "url": "chrome-extension://x/sw.js"}]
                ).encode()
            return _FakeResp(body)
        if "/json/new" in url:
            state["created"] = True
            body = json.dumps(
                {"type": "page", "url": "about:blank", "id": "ABC"}
            ).encode()
            return _FakeResp(body)
        raise AssertionError(f"unexpected urlopen {method} {url}")

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ok = tp.ensure_cdp_page_target("127.0.0.1", 12345, timeout=1.0)
    assert ok is True
    assert state.get("created") is True
    print("PASS  ensure_cdp_page_target creates page from SW-only")


def test_ensure_cdp_page_target_noop_when_page_exists() -> None:
    calls: list[str] = []

    def fake_urlopen(req, timeout=2):  # noqa: ARG001
        url = getattr(req, "full_url", None) or getattr(req, "get_full_url", lambda: "")()
        calls.append(url)
        body = json.dumps([{"type": "page", "url": "chrome://newtab/"}]).encode()
        return _FakeResp(body)

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ok = tp.ensure_cdp_page_target("127.0.0.1", 9999, timeout=1.0)
    assert ok is True
    assert all("/json/new" not in u for u in calls)
    print("PASS  ensure_cdp_page_target noop when page exists")


def test_patch_drission_test_connect_wraps_once() -> None:
    # Build a fake DrissionPage._functions.browser module
    fake_mod = types.ModuleType("DrissionPage._functions.browser")

    def original_test_connect(ip, port):  # noqa: ARG001
        raise AssertionError("original test_connect must not be used after patch")

    fake_mod.test_connect = original_test_connect  # type: ignore[attr-defined]

    # Reset module-level patch flag so this test can re-apply.
    tp._test_connect_patched = False

    ensure_calls = {"n": 0}

    def fake_ensure(ip, port, timeout=1.0):  # noqa: ARG001
        ensure_calls["n"] += 1
        # Fail once (chrome not ready), then succeed (page created).
        return ensure_calls["n"] >= 2

    settings_mod = types.ModuleType("DrissionPage._functions.settings")

    class _S:
        browser_connect_timeout = 5

    settings_mod.Settings = _S  # type: ignore[attr-defined]

    with mock.patch.dict(
        sys.modules,
        {
            "DrissionPage": types.ModuleType("DrissionPage"),
            "DrissionPage._functions": types.ModuleType("DrissionPage._functions"),
            "DrissionPage._functions.browser": fake_mod,
            "DrissionPage._functions.settings": settings_mod,
        },
    ):
        sys.modules["DrissionPage"]._functions = sys.modules["DrissionPage._functions"]  # type: ignore
        sys.modules["DrissionPage._functions"].browser = fake_mod  # type: ignore
        sys.modules["DrissionPage._functions"].settings = settings_mod  # type: ignore
        with mock.patch.object(tp, "ensure_cdp_page_target", side_effect=fake_ensure):
            with mock.patch.object(tp.time, "sleep", return_value=None):
                assert tp.patch_drission_test_connect() is True
                # second call idempotent
                assert tp.patch_drission_test_connect() is True
                wrapped = fake_mod.test_connect
                assert getattr(wrapped, "_grok_page_target_patch", False) is True
                assert getattr(wrapped, "_grok_page_target_original", None) is original_test_connect
                assert wrapped("127.0.0.1", 1) is True
                assert ensure_calls["n"] >= 2
    print("PASS  patch_drission_test_connect replaces once")


def test_create_browser_wires_patch() -> None:
    src = (ROOT / "tab_pool.py").read_text(encoding="utf-8")
    assert "patch_drission_test_connect()" in src
    assert "def ensure_cdp_page_target" in src
    assert "json/new" in src
    mint = (ROOT / "cpa_xai" / "browser_confirm.py").read_text(encoding="utf-8")
    assert "patch_drission_test_connect" in mint
    print("PASS  create_browser / mint wire patch")


def main() -> int:
    test_cdp_target_has_page()
    test_ensure_cdp_page_target_creates_when_only_sw()
    test_ensure_cdp_page_target_noop_when_page_exists()
    test_patch_drission_test_connect_wraps_once()
    test_create_browser_wires_patch()
    print("ALL PASS test_cdp_page_target_patch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
