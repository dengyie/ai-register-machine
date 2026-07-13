#!/usr/bin/env python3
"""Unit checks for Gmail IMAP catch-all provider (no live mailbox required)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def _load_reg():
    _stub_tkinter()
    # DrissionPage / curl_cffi may be present; if not, light stubs.
    if "DrissionPage" not in sys.modules:
        dp = types.ModuleType("DrissionPage")
        dp.Chromium = object
        dp.ChromiumOptions = object
        sys.modules["DrissionPage"] = dp
        err = types.ModuleType("DrissionPage.errors")
        err.PageDisconnectedError = type("PageDisconnectedError", (Exception,), {})
        sys.modules["DrissionPage.errors"] = err
    if "curl_cffi" not in sys.modules:
        curl = types.ModuleType("curl_cffi")
        req = types.ModuleType("curl_cffi.requests")
        curl.requests = req
        sys.modules["curl_cffi"] = curl
        sys.modules["curl_cffi.requests"] = req
    if "proxy_bridge" not in sys.modules:
        pb = types.ModuleType("proxy_bridge")
        pb.resolve_browser_proxy = lambda *a, **k: None
        sys.modules["proxy_bridge"] = pb
    sys.path.insert(0, str(ROOT))
    import grok_register_ttk as reg  # noqa: E402

    return reg


def test_gmail_generate_domain_email() -> None:
    reg = _load_reg()
    reg.config["email_provider"] = "gmail"
    reg.config["gmail_imap_user"] = "catch@gmail.com"
    reg.config["gmail_imap_password"] = "app-pass-16chars"
    reg.config["defaultDomains"] = "example-cf.com, other.com"
    with patch.object(reg, "is_email_used", return_value=False):
        addr, token = reg.gmail_get_email_and_token()
    assert "@" in addr
    domain = addr.split("@", 1)[1]
    assert domain in {"example-cf.com", "other.com"}
    assert token.startswith("gmail:")
    assert token in reg._gmail_token_map
    print("PASS gmail generate domain email")


def test_gmail_dispatch_get_email_and_code() -> None:
    reg = _load_reg()
    reg.config["email_provider"] = "gmail"
    reg.config["gmail_imap_user"] = "catch@gmail.com"
    reg.config["gmail_imap_password"] = "app-pass"
    reg.config["defaultDomains"] = "cf-domain.test"
    with patch.object(reg, "is_email_used", return_value=False):
        with patch.object(reg, "gmail_get_email_and_token", return_value=("a@cf-domain.test", "gmail:t")) as ge:
            out = reg.get_email_and_token()
            assert out == ("a@cf-domain.test", "gmail:t")
            assert ge.called
    with patch.object(reg, "gmail_get_oai_code", return_value="ABC-DEF") as gc:
        code = reg.get_oai_code("gmail:t", "a@cf-domain.test", timeout=1)
        assert code == "ABC-DEF"
        assert gc.called
    print("PASS gmail dispatch")


def test_gmail_imap_extracts_code_with_recipient_match() -> None:
    reg = _load_reg()
    reg.config["gmail_require_recipient_match"] = True
    reg.config["gmail_recent_seconds"] = 900
    reg.config["gmail_imap_last_n"] = 5

    import email.message
    from email.utils import formatdate
    import time as _time

    msg = email.message.EmailMessage()
    msg["From"] = "noreply@x.ai"
    msg["To"] = "rand123@cf-domain.test"
    msg["Subject"] = "XYZ-QWE xAI confirmation code"
    msg["Date"] = formatdate(_time.time(), localtime=False, usegmt=True)
    msg.set_content("Your verification code is XYZ-QWE")

    fake_imap = MagicMock()
    fake_imap.select.return_value = ("OK", [b"1"])
    fake_imap.search.return_value = ("OK", [b"1"])
    fake_imap.fetch.return_value = ("OK", [(b"1 (RFC822)", msg.as_bytes())])

    with patch("imaplib.IMAP4_SSL", return_value=fake_imap):
        code = reg._gmail_imap_get_code(
            "catch@gmail.com",
            "rand123@cf-domain.test",
            "app-pass",
        )
    assert code == "XYZ-QWE"
    fake_imap.login.assert_called_once()
    print("PASS gmail imap extract with recipient match")


def test_gmail_requires_domains() -> None:
    reg = _load_reg()
    reg.config["gmail_imap_user"] = "catch@gmail.com"
    reg.config["gmail_imap_password"] = "app-pass"
    reg.config["defaultDomains"] = ""
    try:
        reg.gmail_get_email_and_token()
        raise AssertionError("expected Exception")
    except Exception as e:
        assert "defaultDomains" in str(e)
    print("PASS gmail requires domains")


def main() -> int:
    test_gmail_generate_domain_email()
    test_gmail_dispatch_get_email_and_code()
    test_gmail_imap_extracts_code_with_recipient_match()
    test_gmail_requires_domains()
    print("\nALL PASS (gmail imap provider)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
