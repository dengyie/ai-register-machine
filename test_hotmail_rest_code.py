#!/usr/bin/env python3
"""Static + optional live checks for Hotmail REST auto code fetch."""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _stub_tkinter() -> None:
    class _Dummy:
        def __getattr__(self, name):
            return _Dummy()

        def __call__(self, *a, **k):
            return _Dummy()

    class _TkModule(types.ModuleType):
        def __init__(self):
            super().__init__("tkinter")

        def __getattr__(self, name):
            if name in ("ttk", "messagebox", "scrolledtext"):
                return _Dummy()
            if name in ("StringVar", "IntVar", "BooleanVar", "DoubleVar"):
                return type(
                    name,
                    (),
                    {
                        "__init__": lambda *a, **k: None,
                        "get": lambda self: "",
                        "set": lambda self, v: None,
                    },
                )
            return type(
                name,
                (),
                {
                    "__init__": lambda *a, **k: None,
                    "pack": lambda *a, **k: None,
                    "grid": lambda *a, **k: None,
                    "bind": lambda *a, **k: None,
                    "configure": lambda *a, **k: None,
                    "after": lambda *a, **k: None,
                    "title": lambda *a, **k: None,
                    "geometry": lambda *a, **k: None,
                    "mainloop": lambda *a, **k: None,
                    "protocol": lambda *a, **k: None,
                    "destroy": lambda *a, **k: None,
                    "winfo_exists": lambda *a, **k: 0,
                },
            )()

    sys.modules["tkinter"] = _TkModule()
    sys.modules["tkinter.ttk"] = _Dummy()
    sys.modules["tkinter.messagebox"] = _Dummy()
    sys.modules["tkinter.scrolledtext"] = _Dummy()


def test_split_regex_not_eating_s() -> None:
    # regression: r"[,，\\s]+" treated letter s as separator
    bad = re.split(r"[,，\\s]+", "rest,imap")
    good = re.split(r"[,，\s]+", "rest,imap")
    assert good == ["rest", "imap"], good
    assert bad != good
    print("PASS  split regex")


def test_modes_and_rest_helpers() -> None:
    _stub_tkinter()
    sys.path.insert(0, str(ROOT))
    import grok_register_ttk as reg  # noqa: E402

    reg.config["hotmail_mail_fetch_modes"] = "rest,imap"
    modes = reg._hotmail_mail_fetch_modes()
    assert modes[0] == "rest", modes
    assert "imap" in modes
    assert hasattr(reg, "_hotmail_rest_get_code")
    assert hasattr(reg, "_hotmail_parse_iso_ts")
    ts = reg._hotmail_parse_iso_ts("2026-07-11T16:47:54Z")
    assert ts > 0
    # subject-only extraction used by REST path
    code = reg.extract_verification_code(
        "Validate your email ...",
        "YWM-1ZD xAI confirmation code",
    )
    assert code == "YWM-1ZD", code
    print("PASS  modes/rest helpers", modes)


def test_live_rest_if_credentials() -> None:
    """Live mailbox smoke — opt-in only.

    Requires:
      - GROK_REGISTER_LIVE=1
      - local mail_credentials.txt (gitignored)
    Never enable this in CI.
    """
    import os

    if os.environ.get("GROK_REGISTER_LIVE", "").strip() not in {"1", "true", "TRUE", "yes", "YES"}:
        print("SKIP  live REST (set GROK_REGISTER_LIVE=1 to enable)")
        return
    cred = ROOT / "mail_credentials.txt"
    if not cred.is_file():
        print("SKIP  live REST (no mail_credentials.txt)")
        return
    _stub_tkinter()
    sys.path.insert(0, str(ROOT))
    import grok_register_ttk as reg  # noqa: E402

    # Prefer reading historical code for a known alias via REST without waiting new mail:
    # use main mailbox + require_recipient false temporarily if needed.
    accounts = reg._hotmail_load_accounts(force=True)
    acc = accounts[0]
    token = reg.hotmail_refresh_access_token(acc, log_callback=print)
    # Smoke against historical inbox: widen window so old confirmation mails count.
    # Real registration still uses config hotmail_recent_seconds (default 900).
    old_match = reg.config.get("hotmail_require_recipient_match", True)
    old_recent = reg.config.get("hotmail_recent_seconds", 900)
    reg.config["hotmail_require_recipient_match"] = False
    reg.config["hotmail_recent_seconds"] = max(int(old_recent or 900), 86400 * 3)
    try:
        code = reg._hotmail_rest_get_code(
            acc["email"],
            acc["email"],
            token,
            log_callback=print,
        )
    finally:
        reg.config["hotmail_require_recipient_match"] = old_match
        reg.config["hotmail_recent_seconds"] = old_recent
    assert code, "REST should return a recent verification code from inbox"
    print("PASS  live REST code", code)


def main() -> int:
    test_split_regex_not_eating_s()
    test_modes_and_rest_helpers()
    test_live_rest_if_credentials()
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
