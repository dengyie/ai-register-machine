#!/usr/bin/env python3
"""Offline tests for orphan Drission Chrome cleanup predicates."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_is_drission_chrome_cmdline() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from tab_pool import is_drission_chrome_cmdline

    live = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
        "--remote-debugging-port=12231 "
        "--user-data-dir=/tmp/DrissionPage/autoPortData/12231 "
        "--load-extension=/proj/turnstilePatch"
    )
    assert is_drission_chrome_cmdline(live)

    helper = live.replace("Google Chrome ", "Google Chrome Helper ")
    # still has Google Chrome Helper in path - our check is "Helper" in cmd
    assert not is_drission_chrome_cmdline(
        "/Apps/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper "
        "--type=renderer --remote-debugging-port=12231 --user-data-dir=/tmp/DrissionPage/autoPortData/1"
    )

    unrelated = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
        "--remote-debugging-port=9222 --user-data-dir=/Users/me/chrome-coinbot-profile"
    )
    assert not is_drission_chrome_cmdline(unrelated)

    no_port = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/x"
    assert not is_drission_chrome_cmdline(no_port)
    print("PASS is_drission_chrome_cmdline")


def test_parse_ps_chrome_rows_filters_ppid() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from tab_pool import parse_ps_chrome_rows

    ps = """
 10738 10734 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=12231 --user-data-dir=/tmp/DrissionPage/autoPortData/12231 --load-extension=/x/turnstilePatch
 28307     1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=52082 --user-data-dir=/tmp/DrissionPage/autoPortData/52082 --load-extension=/x/turnstilePatch
 89427     1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222 --user-data-dir=/Users/me/chrome-coinbot-profile
 99999     1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome Helper --type=gpu --remote-debugging-port=52082 --user-data-dir=/tmp/DrissionPage/autoPortData/52082
"""
    rows = parse_ps_chrome_rows(ps)
    pids = {r[0] for r in rows}
    assert 10738 in pids
    assert 28307 in pids
    assert 89427 not in pids  # unrelated profile
    assert 99999 not in pids  # helper
    orphan = [r for r in rows if r[1] in (0, 1)]
    assert {r[0] for r in orphan} == {28307}
    print("PASS parse_ps_chrome_rows_filters_ppid")


def test_cleanup_dry_run_only_ppid_init() -> None:
    """dry_run path should not raise; real kill is OS-dependent so we only dry-run."""
    import sys

    sys.path.insert(0, str(ROOT))
    from tab_pool import cleanup_orphan_drission_chromes

    logs: list[str] = []
    res = cleanup_orphan_drission_chromes(
        log_callback=logs.append,
        only_ppid_init=True,
        dry_run=True,
    )
    assert "scanned" in res
    assert "matched" in res
    assert res.get("dry_run") is True
    assert isinstance(res.get("pids"), list)
    print("PASS cleanup_dry_run_only_ppid_init")


def test_is_xvfb_and_parse() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from tab_pool import is_xvfb_cmdline, parse_ps_xvfb_rows

    assert is_xvfb_cmdline("/usr/bin/Xvfb :99 -screen 0 1280x900x24")
    assert not is_xvfb_cmdline("xvfb-run -a python")
    rows = parse_ps_xvfb_rows(
        " 10 1 /usr/bin/Xvfb :99 -screen 0 1x1x24\n 11 2 /usr/bin/Xvfb :100\n"
    )
    assert {r[0] for r in rows} == {10, 11}
    print("PASS is_xvfb_and_parse")


def test_cleanup_orphan_xvfb_dry_run() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from tab_pool import cleanup_orphan_xvfb

    res = cleanup_orphan_xvfb(only_ppid_init=True, dry_run=True, clean_tmp_dirs=False)
    assert res.get("dry_run") is True
    assert "scanned" in res
    print("PASS cleanup_orphan_xvfb_dry_run")


def main() -> int:
    test_is_drission_chrome_cmdline()
    test_parse_ps_chrome_rows_filters_ppid()
    test_cleanup_dry_run_only_ppid_init()
    test_is_xvfb_and_parse()
    test_cleanup_orphan_xvfb_dry_run()
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
