#!/usr/bin/env python3
"""Tests: consent Allow must set action=allow and never default-submit Deny."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_consent_helpers_pure() -> None:
    from cpa_xai import browser_confirm as bc

    assert bc._is_device_done(
        "https://accounts.x.ai/oauth2/device/done", "设备已授权"
    )
    assert bc._is_device_done("https://x/oauth2/device/done?x=1", "ok")
    assert not bc._is_device_done(
        "https://accounts.x.ai/oauth2/device/consent?user_code=A", "授权 Grok Build"
    )

    assert bc._is_consent_page(
        "https://accounts.x.ai/oauth2/device/consent?user_code=A", ""
    )
    assert bc._is_consent_page("https://accounts.x.ai/account", "授权 Grok Build")
    assert bc._is_consent_page("https://x", "Authorize Grok Build")
    assert not bc._is_consent_page("https://accounts.x.ai/oauth2/device", "继续")

    # After 3 failed consent actions → reopen once budget
    assert bc._consent_should_reopen(consent_action_n=3, reopens=0, max_actions=3) is True
    assert bc._consent_should_reopen(consent_action_n=2, reopens=0, max_actions=3) is False
    assert bc._consent_should_reopen(consent_action_n=5, reopens=1, max_actions=3) is False
    print("PASS  consent helpers pure")


def test_prepare_consent_allow_sets_action() -> None:
    from cpa_xai import browser_confirm as bc

    logs: list[str] = []
    calls: list[str] = []

    class FakePage:
        def run_js(self, code: str):
            calls.append(code)
            assert "action" in code
            assert "allow" in code
            # must not bare-submit without allow button preference
            assert "return 'form_submit'" not in code or "btn.click" in code
            return "ok:allow"

    ret = bc._prepare_consent_allow_form(FakePage(), logs.append)
    assert ret == "ok:allow"
    assert any("consent prepare action=allow" in m for m in logs)
    assert calls, "expected run_js"
    # preparation JS must skip cookie forms and prefer 允许 button semantics
    js = calls[0]
    assert "隐私偏好" in js or "全部允许" in js
    assert "allow" in js
    print("PASS  prepare_consent_allow_sets_action")


def test_consent_js_fallback_never_bare_submit_first() -> None:
    """Reject path that form.submit() without clicking 允许 (Deny is first submit)."""
    src = (ROOT / "cpa_xai" / "browser_confirm.py").read_text(encoding="utf-8")
    assert "def _prepare_consent_allow_form" in src
    assert "_prepare_consent_allow_form(page" in src
    # After prepare, real click; stuck → controlled reopen
    assert "consent_stuck" in src or "consent stuck" in src
    assert "reopen device" in src.lower() or "reopen_device" in src or "reopen device uri" in src
    # JS fallback must click allow button; bare f.submit is dangerous (拒绝 first)
    # Allow form_submit only after explicit action=allow AND no allow button found
    # and must log it — still better to prefer btn only
    assert "btn_click:" in src
    # prepare must run before real click in consent branch
    consent_idx = src.find("# Consent page")
    assert consent_idx > 0
    chunk = src[consent_idx : consent_idx + 3500]
    prep = chunk.find("_prepare_consent_allow_form")
    real = chunk.find("real=True")
    assert 0 <= prep < real, "prepare must run before real click"
    print("PASS  consent js fallback / prepare-before-click markers")


def test_prepare_skips_cookie_form() -> None:
    from cpa_xai import browser_confirm as bc

    class FakePage:
        def run_js(self, code: str):  # noqa: ARG002
            return "skip_cookie_form"

    logs: list[str] = []
    ret = bc._prepare_consent_allow_form(FakePage(), logs.append)
    assert ret == "skip_cookie_form"
    print("PASS  prepare_skips_cookie_form")


def test_wait_post_allow_state_reaches_done() -> None:
    """Mid-nav consent URL then device/done must classify as done, not shell."""
    from cpa_xai import browser_confirm as bc

    class FakePage:
        def __init__(self) -> None:
            self.n = 0
            self.urls = [
                "https://accounts.x.ai/oauth2/device/consent?user_code=A",
                "https://accounts.x.ai/oauth2/device/consent?user_code=A",
                "https://accounts.x.ai/oauth2/device/done",
            ]
            self.texts = [
                "授权 Grok Build 允许",
                "授权 Grok Build 允许",
                "设备已授权 您的设备已获授权",
            ]

        @property
        def url(self) -> str:
            return self.urls[min(self.n, len(self.urls) - 1)]

        def run_js(self, code: str):  # noqa: ARG002
            # _visible_text path may use run_js; advance on body text reads
            if "document.body" in code or "innerText" in code:
                t = self.texts[min(self.n, len(self.texts) - 1)]
                self.n += 1
                return t
            return ""

    # Patch page helpers to drive FakePage without DrissionPage
    seq = {"i": 0}
    urls = [
        "https://accounts.x.ai/oauth2/device/consent?user_code=A",
        "https://accounts.x.ai/oauth2/device/consent?user_code=A",
        "https://accounts.x.ai/oauth2/device/done",
    ]
    texts = [
        "授权 Grok Build 允许",
        "授权 Grok Build 允许",
        "设备已授权 您的设备已获授权",
    ]

    def fake_url(_p):  # noqa: ANN001
        return urls[min(seq["i"], len(urls) - 1)]

    def fake_text(_p):  # noqa: ANN001
        i = seq["i"]
        t = texts[min(i, len(texts) - 1)]
        seq["i"] = i + 1
        return t

    with mock.patch.object(bc, "_page_url", side_effect=fake_url), mock.patch.object(
        bc, "_visible_text", side_effect=fake_text
    ), mock.patch.object(bc.time, "sleep", return_value=None):
        u, t = bc._wait_post_allow_state(object(), lambda _m: None, budget_sec=2.0)
    assert bc._is_device_done(u, t)
    assert not bc._is_consent_page(u, t)
    print("PASS  wait_post_allow_state_reaches_done")


def test_oauth_hard_invalid_grant_tax_marker() -> None:
    od = (ROOT / "cpa_xai" / "oauth_device.py").read_text(encoding="utf-8")
    assert "tax=server_policy" in od
    assert "consent may have succeeded" in od
    print("PASS  oauth_hard_invalid_grant_tax_marker")


def main() -> int:
    # Import will fail until helpers exist — TDD red then green.
    test_consent_helpers_pure()
    test_prepare_consent_allow_sets_action()
    test_consent_js_fallback_never_bare_submit_first()
    test_prepare_skips_cookie_form()
    test_wait_post_allow_state_reaches_done()
    test_oauth_hard_invalid_grant_tax_marker()
    print("ALL PASS test_mint_consent_allow")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
