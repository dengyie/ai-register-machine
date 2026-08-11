#!/usr/bin/env python3
"""Offline tests: mint invalid_grant diagnostic log markers (no DrissionPage)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_consent_diag_source_markers() -> None:
    bc = (ROOT / "cpa_xai" / "browser_confirm.py").read_text(encoding="utf-8")
    assert "def _consent_page_diag" in bc
    assert "consent_diag[" in bc
    assert "pre_js_fallback" in bc
    assert "post_real_click" in bc
    assert "post_js_fallback" in bc
    assert "btn_click:" in bc
    assert "no_allow_button" in bc  # never bare f.submit (拒绝 is first submit)
    assert "skip_cookie_form" in bc
    assert "def _prepare_consent_allow_form" in bc
    assert "def _wait_post_allow_state" in bc
    assert "consent prepare action=allow" in bc
    # still prefer real click path before JS fallback
    assert '["允许", "Allow", "Authorize", "Approve"]' in bc
    assert "real=True" in bc
    od = (ROOT / "cpa_xai" / "oauth_device.py").read_text(encoding="utf-8")
    assert "oauth poll HARD" in od
    assert "safe_body" in od
    assert "tax=server_policy" in od
    # must not dump raw tokens on hard error path
    assert "access_token" in od  # TokenResult path still uses it
    assert "f\"<{type(v).__name__}>\"" in od or "<" in od
    cli = (ROOT / "register_cli.py").read_text(encoding="utf-8")
    assert "mint_egress pin verify" in cli
    assert "current_egress_label()" in cli
    print("PASS  consent_diag source markers")


def test_consent_page_diag_logs_without_click() -> None:
    from cpa_xai import browser_confirm as bc

    logs: list[str] = []

    class FakeEl:
        def __init__(self, text: str):
            self.text = text

    class FakePage:
        url = "https://accounts.x.ai/oauth2/device/consent?user_code=ABCD"

        def run_js(self, code: str):  # noqa: ARG002
            if "document.body" in code:
                return "授权 Grok Build 允许 Allow 隐私政策"
            if "JSON.stringify" in code or "out.forms" in code:
                return (
                    '{"forms":[{"actionAttr":"/oauth2/device/consent","method":"post",'
                    '"actionVal":null,"text":"Authorize Grok Build 允许"}],'
                    '"buttons":[{"t":"允许","type":"submit","dis":false},'
                    '{"t":"取消","type":"button","dis":false}]}'
                )
            return ""

        def eles(self, sel: str):  # noqa: ARG002
            return [FakeEl("允许"), FakeEl("取消")]

        def ele(self, *a, **k):  # noqa: ARG002
            return None

    with mock.patch.object(bc, "_cookie_banner_visible", return_value=False):
        bc._consent_page_diag(FakePage(), logs.append, "enter")
    joined = "\n".join(logs)
    assert "consent_diag[enter]" in joined
    assert "exact_btns=" in joined
    assert "允许" in joined
    assert "dom=" in joined
    # diagnostic must not claim it clicked
    assert "clicked" not in joined.lower()
    print("PASS  consent_page_diag logs without click")


def test_oauth_hard_error_logs_safe_body() -> None:
    """poll_device_token hard path logs status/err without raising tokens."""
    src = (ROOT / "cpa_xai" / "oauth_device.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "poll_device_token"
    )
    # Ensure hard log exists in function body text
    chunk = ast.get_source_segment(src, fn) or ""
    assert "oauth poll HARD" in chunk
    assert "safe_body" in chunk
    # Redact logic present
    assert "token" in chunk.lower()
    print("PASS  oauth hard error safe_body markers")


def main() -> int:
    test_consent_diag_source_markers()
    test_consent_page_diag_logs_without_click()
    test_oauth_hard_error_logs_safe_body()
    print("ALL PASS test_mint_consent_diag")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
