#!/usr/bin/env python3
"""Lifecycle gates: mint browser owned/release/shutdown + register worker cleanup."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent


def test_release_mint_browser_owned_closes() -> None:
    """owned=True must close the explicit browser (was a silent no-op)."""
    sys.path.insert(0, str(ROOT))
    from cpa_xai import browser_confirm as bc

    closed: list[object] = []

    class FakeBrowser:
        process_id = None

        def quit(self, *a, **k):
            closed.append(self)

    fake = FakeBrowser()
    with mock.patch.object(bc, "close_standalone", side_effect=lambda b: closed.append(b)):
        bc.release_mint_browser(owned=True, browser=fake, success=True)
    assert fake in closed, "owned release must close browser"
    print("PASS  release_mint_browser owned closes browser")


def test_release_mint_browser_owned_without_browser_no_crash() -> None:
    sys.path.insert(0, str(ROOT))
    from cpa_xai import browser_confirm as bc

    # No browser arg and empty TLS — must not raise
    bc.release_mint_browser(owned=True, browser=None, success=True)
    print("PASS  release owned without browser no crash")


def test_shutdown_mint_browsers_closes_registry() -> None:
    sys.path.insert(0, str(ROOT))
    from cpa_xai import browser_confirm as bc

    closed: list[object] = []

    class FakeBrowser:
        process_id = None

        def quit(self, *a, **k):
            closed.append(self)

    fake = FakeBrowser()
    bc._mint_registry_add(fake)
    # Ensure not in TLS so only registry path hits
    st = bc._mint_tls_get()
    st["browser"] = None
    st["page"] = None

    with mock.patch.object(bc, "close_standalone", side_effect=lambda b: closed.append(b) or bc._mint_registry_drop(b)):
        bc.shutdown_mint_browsers()
    assert fake in closed
    with bc._mint_browsers_lock:
        assert fake not in bc._mint_browsers_all
    print("PASS  shutdown_mint_browsers closes registry leftovers")


def test_acquire_registers_browser() -> None:
    sys.path.insert(0, str(ROOT))
    from cpa_xai import browser_confirm as bc

    class FakeBrowser:
        process_id = 0
        tab_ids = []

    fake_b = FakeBrowser()
    fake_p = object()

    # Clear state
    st = bc._mint_tls_get()
    st["browser"] = None
    st["page"] = None
    st["served"] = 0
    with bc._mint_browsers_lock:
        bc._mint_browsers_all.clear()

    with mock.patch.object(bc, "create_standalone_page", return_value=(fake_b, fake_p)):
        with mock.patch.object(bc, "clear_page_session"):
            b, p, owned = bc.acquire_mint_browser(reuse=False, log=lambda m: None)
    assert b is fake_b and p is fake_p and owned is True
    with bc._mint_browsers_lock:
        assert fake_b in bc._mint_browsers_all
    # cleanup
    bc._mint_registry_drop(fake_b)
    print("PASS  acquire_mint_browser registers browser")


def test_register_cli_worker_exit_shuts_mint() -> None:
    """Inline mint cleanup: register worker exit must call shutdown_mint_browsers."""
    src = (ROOT / "register_cli.py").read_text(encoding="utf-8")
    # register worker exit block must include mint shutdown (not only mint_worker)
    assert "register worker exit" in src
    # Both call sites
    assert src.count("shutdown_mint_browsers()") >= 3  # reg worker + mint worker + main
    assert "Inline mint" in src or "inline-mint" in src or "mint_workers=0" in src
    # main post-join sweep
    assert "Sweep any mint Chromium" in src or "process registry" in src
    print("PASS  register_cli shuts mint on reg worker + main")


def test_mint_with_browser_finally_uses_release_owned() -> None:
    src = (ROOT / "cpa_xai" / "browser_confirm.py").read_text(encoding="utf-8")
    assert "release_mint_browser(" in src
    assert "owned=True" in src
    assert "browser=own_browser" in src
    # no silent owned return without close
    assert "# owned browser not in tls" not in src
    print("PASS  mint_with_browser finally release owned path")


def test_hybrid_default_in_config_and_ttk() -> None:
    ttk = (ROOT / "grok_register_ttk.py").read_text(encoding="utf-8")
    cfg = (ROOT / "config.example.json").read_text(encoding="utf-8")
    # DEFAULT_CONFIG + PERF_FLAGS
    assert '"browser_recycle_mode": "hybrid"' in ttk
    assert '"browser_recycle_mode": "hybrid"' in cfg
    assert '"browser_recycle_every": 15' in ttk or "'browser_recycle_every': 15" in ttk
    print("PASS  hybrid default config")


def test_clear_session_cookie_fail_returns_false() -> None:
    """clear_session must return False when cookie wipe cannot run (hard recycle)."""
    sys.path.insert(0, str(ROOT))
    import tab_pool as tp

    class BoomBrowser:
        tab_ids = []
        process_id = None

        def cookies(self):
            raise RuntimeError("no cookies api")

    class BoomTab:
        def get(self, *a, **k):
            pass

        def run_js(self, *a, **k):
            pass

    # Install fake browser on this thread's TLS
    tp.TabPool._thread_local.browser = BoomBrowser()
    tp.TabPool._thread_local.tab = BoomTab()
    tp.TabPool._thread_local.served = 0
    try:
        ok = tp.TabPool.clear_session(log_callback=lambda m: None)
        assert ok is False, "cookie wipe total failure must return False"
    finally:
        tp.TabPool._thread_local.browser = None
        tp.TabPool._thread_local.tab = None
    print("PASS  clear_session returns False when cookies cannot wipe")


def test_clear_session_source_has_service_worker() -> None:
    src = (ROOT / "tab_pool.py").read_text(encoding="utf-8")
    assert "serviceWorker" in src
    assert "cookies_ok=" in src
    assert "cookie wipe incomplete" in src
    print("PASS  clear_session SW + cookies_ok source")


if __name__ == "__main__":
    test_release_mint_browser_owned_closes()
    test_release_mint_browser_owned_without_browser_no_crash()
    test_shutdown_mint_browsers_closes_registry()
    test_acquire_registers_browser()
    test_register_cli_worker_exit_shuts_mint()
    test_mint_with_browser_finally_uses_release_owned()
    test_hybrid_default_in_config_and_ttk()
    test_clear_session_cookie_fail_returns_false()
    test_clear_session_source_has_service_worker()
    print("ALL PASS")
