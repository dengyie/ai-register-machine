#!/usr/bin/env python3
"""Unit checks for light browser fingerprint mode (tier A, A/B arm).

No live Chromium — option factory + resolver + CLI surface only.
"""

from __future__ import annotations

import os
import random
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stub_heavy_deps() -> None:
    """Allow importing grok_register_ttk without tkinter/DrissionPage in bare CI."""
    if "tkinter" not in sys.modules:
        tk = types.ModuleType("tkinter")
        tk.StringVar = object
        tk.BooleanVar = object
        tk.END = "end"
        for attr in ("W", "E", "N", "S", "EW", "BOTH", "X", "Y", "LEFT"):
            setattr(tk, attr, attr.lower())
        tk.Tk = type("Tk", (), {})
        sys.modules["tkinter"] = tk
        for sub in ("ttk", "messagebox", "scrolledtext"):
            sys.modules[f"tkinter.{sub}"] = types.ModuleType(f"tkinter.{sub}")
    if "DrissionPage" not in sys.modules:
        dp = types.ModuleType("DrissionPage")
        dp.Chromium = object
        dp.ChromiumOptions = object
        sys.modules["DrissionPage"] = dp
        err = types.ModuleType("DrissionPage.errors")
        err.PageDisconnectedError = type("PageDisconnectedError", (Exception,), {})
        sys.modules["DrissionPage.errors"] = err
    if "curl_cffi" not in sys.modules:
        cc = types.ModuleType("curl_cffi")
        req = types.ModuleType("curl_cffi.requests")
        cc.requests = req
        sys.modules["curl_cffi"] = cc
        sys.modules["curl_cffi.requests"] = req


def _load():
    _stub_heavy_deps()
    import grok_register_ttk as m

    return m


def test_resolve_mode_defaults_off() -> None:
    ttk = _load()
    old_env = os.environ.pop("BROWSER_FINGERPRINT_MODE", None)
    old_cfg = ttk.config.get("browser_fingerprint_mode")
    old_perf = ttk.PERF_FLAGS.get("browser_fingerprint_mode")
    try:
        ttk.config["browser_fingerprint_mode"] = "off"
        ttk.PERF_FLAGS["browser_fingerprint_mode"] = "off"
        assert ttk.resolve_browser_fingerprint_mode() == "off"
        assert ttk.resolve_browser_fingerprint_mode("light") == "light"
        assert ttk.resolve_browser_fingerprint_mode("OFF") == "off"
        assert ttk.resolve_browser_fingerprint_mode("random") == "light"
        os.environ["BROWSER_FINGERPRINT_MODE"] = "light"
        ttk.config["browser_fingerprint_mode"] = "off"
        ttk.PERF_FLAGS["browser_fingerprint_mode"] = "off"
        # explicit still wins
        assert ttk.resolve_browser_fingerprint_mode("off") == "off"
        # PERF/config empty → env light
        ttk.PERF_FLAGS["browser_fingerprint_mode"] = ""
        ttk.config["browser_fingerprint_mode"] = ""
        assert ttk.resolve_browser_fingerprint_mode() == "light"
    finally:
        if old_env is None:
            os.environ.pop("BROWSER_FINGERPRINT_MODE", None)
        else:
            os.environ["BROWSER_FINGERPRINT_MODE"] = old_env
        if old_cfg is not None:
            ttk.config["browser_fingerprint_mode"] = old_cfg
        if old_perf is not None:
            ttk.PERF_FLAGS["browser_fingerprint_mode"] = old_perf
    print("PASS  resolve_browser_fingerprint_mode")


def test_pick_light_fingerprint_deterministic() -> None:
    ttk = _load()
    a = ttk.pick_light_fingerprint(rng=random.Random(42))
    b = ttk.pick_light_fingerprint(rng=random.Random(42))
    assert a == b
    assert a["mode"] == "light"
    assert a["user_agent"] in ttk._LIGHT_UA_POOL
    assert (a["width"], a["height"]) in ttk._LIGHT_VIEWPORTS
    assert a["lang"]
    assert a["accept_lang"]
    c = ttk.pick_light_fingerprint(rng=random.Random(7))
    d = ttk.pick_light_fingerprint(rng=random.Random(99))
    assert c["user_agent"] and d["user_agent"]
    print("PASS  pick_light_fingerprint deterministic")


def test_apply_light_fingerprint_sets_options() -> None:
    ttk = _load()
    opts = MagicMock()
    fp = {
        "user_agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
        "width": 1440,
        "height": 900,
        "lang": "en-GB",
        "accept_lang": "en-GB,en;q=0.9",
    }
    out = ttk.apply_light_fingerprint(opts, fp)
    opts.set_user_agent.assert_called_with(fp["user_agent"])
    args = [c.args[0] for c in opts.set_argument.call_args_list]
    assert "--window-size=1440,900" in args
    assert "--lang=en-GB" in args
    assert "--accept-lang=en-GB,en;q=0.9" in args
    assert out["mode"] == "light"
    assert ttk._LAST_BROWSER_FINGERPRINT == out
    print("PASS  apply_light_fingerprint sets options")


def test_create_browser_options_light_applies_pool() -> None:
    ttk = _load()

    class FakeOpts:
        def __init__(self):
            self.arguments = []
            self.ua = None

        def auto_port(self):
            return None

        def set_timeouts(self, **kw):
            return None

        def set_argument(self, flag):
            self.arguments.append(flag)

        def set_user_agent(self, ua):
            self.ua = ua

        def headless(self, v):
            return None

        def add_extension(self, path):
            return None

    old_co = ttk.ChromiumOptions
    old_env = os.environ.pop("BROWSER_FINGERPRINT_MODE", None)
    old_seed = os.environ.pop("BROWSER_FINGERPRINT_SEED", None)
    old_mode_cfg = ttk.config.get("browser_fingerprint_mode")
    old_mode_perf = ttk.PERF_FLAGS.get("browser_fingerprint_mode")
    old_headless = ttk.config.get("browser_headless")
    try:
        ttk.ChromiumOptions = FakeOpts  # type: ignore
        ttk.config["browser_headless"] = True  # skip DISPLAY check
        ttk.config["browser_fingerprint_mode"] = "light"
        ttk.PERF_FLAGS["browser_fingerprint_mode"] = "light"
        os.environ["BROWSER_FINGERPRINT_SEED"] = "12345"
        opts = ttk.create_browser_options(browser_proxy="", apply_config_proxy=False)
        assert isinstance(opts, FakeOpts)
        assert opts.ua  # light set UA even in headless
        assert any(str(a).startswith("--window-size=") for a in opts.arguments)
        assert any(str(a).startswith("--lang=") for a in opts.arguments)
        assert ttk._LAST_BROWSER_FINGERPRINT and ttk._LAST_BROWSER_FINGERPRINT.get("mode") == "light"
        # off mode
        ttk.config["browser_fingerprint_mode"] = "off"
        ttk.PERF_FLAGS["browser_fingerprint_mode"] = "off"
        os.environ.pop("BROWSER_FINGERPRINT_MODE", None)
        opts2 = ttk.create_browser_options(browser_proxy="", apply_config_proxy=False)
        assert ttk._LAST_BROWSER_FINGERPRINT == {"mode": "off"}
        assert any(str(a) == "--window-size=1280,900" for a in opts2.arguments)
    finally:
        ttk.ChromiumOptions = old_co
        if old_env is None:
            os.environ.pop("BROWSER_FINGERPRINT_MODE", None)
        else:
            os.environ["BROWSER_FINGERPRINT_MODE"] = old_env
        if old_seed is None:
            os.environ.pop("BROWSER_FINGERPRINT_SEED", None)
        else:
            os.environ["BROWSER_FINGERPRINT_SEED"] = old_seed
        if old_mode_cfg is not None:
            ttk.config["browser_fingerprint_mode"] = old_mode_cfg
        if old_mode_perf is not None:
            ttk.PERF_FLAGS["browser_fingerprint_mode"] = old_mode_perf
        if old_headless is not None:
            ttk.config["browser_headless"] = old_headless
    print("PASS  create_browser_options light vs off")


def test_register_cli_surface() -> None:
    src = (ROOT / "register_cli.py").read_text(encoding="utf-8")
    assert "--browser-fingerprint-mode" in src
    assert "browser_fingerprint_mode" in src
    assert 'choices=("off", "light")' in src or "choices=('off', 'light')" in src
    ttk_src = (ROOT / "grok_register_ttk.py").read_text(encoding="utf-8")
    assert "def resolve_browser_fingerprint_mode" in ttk_src
    assert "def pick_light_fingerprint" in ttk_src
    assert "def apply_light_fingerprint" in ttk_src
    assert '"browser_fingerprint_mode"' in ttk_src
    cfg = (ROOT / "config.example.json").read_text(encoding="utf-8")
    assert "browser_fingerprint_mode" in cfg
    print("PASS  register_cli + ttk + config.example surface")


def main() -> int:
    test_resolve_mode_defaults_off()
    test_pick_light_fingerprint_deterministic()
    test_apply_light_fingerprint_sets_options()
    test_create_browser_options_light_applies_pool()
    test_register_cli_surface()
    print("ALL PASS test_browser_fingerprint_light")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
