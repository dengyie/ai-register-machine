#!/usr/bin/env python3
"""Offline checks for SSO cookie normalization."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_normalize_sso_cookie() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from cpa_xai.accounts import normalize_sso_cookie

    jwt = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.abc.def"
    assert normalize_sso_cookie(jwt) == jwt
    assert normalize_sso_cookie("-" + jwt) == jwt
    assert normalize_sso_cookie("  -" + jwt + "  ") == jwt
    assert normalize_sso_cookie("") == ""
    assert normalize_sso_cookie(None) == ""
    # do not strip arbitrary leading dash without eyJ nearby
    assert normalize_sso_cookie("-session-id-value") == "-session-id-value"
    print("PASS normalize_sso_cookie")


def test_parse_accounts_strips_leading_dash() -> None:
    import sys
    import tempfile

    sys.path.insert(0, str(ROOT))
    from cpa_xai.accounts import parse_accounts_file

    jwt = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.abc.def"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "accounts_cli.txt"
        p.write_text(f"a@x.com----pw-----{jwt}\n", encoding="utf-8")
        rows = parse_accounts_file(p)
        assert len(rows) == 1
        assert rows[0].sso == jwt
    print("PASS parse_accounts_strips_leading_dash")


def main() -> int:
    test_normalize_sso_cookie()
    test_parse_accounts_strips_leading_dash()
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
