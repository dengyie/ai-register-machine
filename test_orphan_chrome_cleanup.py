#!/usr/bin/env python3
"""Offline tests for orphan Drission Chrome cleanup predicates."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent


def test_is_drission_chrome_cmdline() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from tab_pool import is_drission_chrome_cmdline, is_drission_related_cmdline

    live = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
        "--remote-debugging-port=12231 "
        "--user-data-dir=/tmp/DrissionPage/autoPortData/12231 "
        "--load-extension=/proj/turnstilePatch"
    )
    assert is_drission_chrome_cmdline(live)
    assert is_drission_related_cmdline(live)

    helper = (
        "/Apps/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper "
        "--type=renderer --remote-debugging-port=12231 "
        "--user-data-dir=/tmp/DrissionPage/autoPortData/12231"
    )
    assert not is_drission_chrome_cmdline(helper)
    assert is_drission_related_cmdline(helper)

    unrelated = (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
        "--remote-debugging-port=9222 --user-data-dir=/Users/me/chrome-coinbot-profile"
    )
    assert not is_drission_chrome_cmdline(unrelated)
    assert not is_drission_related_cmdline(unrelated)

    no_port = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/x"
    assert not is_drission_chrome_cmdline(no_port)
    print("PASS is_drission_chrome_cmdline")


def test_parse_ps_chrome_rows_filters_ppid() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from tab_pool import parse_ps_chrome_rows, parse_ps_drission_related_rows

    ps = """
 10738 10734 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=12231 --user-data-dir=/tmp/DrissionPage/autoPortData/12231 --load-extension=/x/turnstilePatch
 28307     1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=52082 --user-data-dir=/tmp/DrissionPage/autoPortData/52082 --load-extension=/x/turnstilePatch
 89427     1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=9222 --user-data-dir=/Users/me/chrome-coinbot-profile
 99999     1 /Applications/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper --type=gpu --remote-debugging-port=52082 --user-data-dir=/tmp/DrissionPage/autoPortData/52082
"""
    rows = parse_ps_chrome_rows(ps)
    pids = {r[0] for r in rows}
    assert 10738 in pids
    assert 28307 in pids
    assert 89427 not in pids  # unrelated profile
    assert 99999 not in pids  # helper
    orphan = [r for r in rows if r[1] in (0, 1)]
    assert {r[0] for r in orphan} == {28307}

    related = parse_ps_drission_related_rows(ps)
    related_pids = {r[0] for r in related}
    assert 99999 in related_pids  # helpers included
    assert 89427 not in related_pids
    print("PASS parse_ps_chrome_rows_filters_ppid")


def test_family_pids_include_helpers() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from tab_pool import _family_pids_for_main

    main_cmd = (
        "chrome --remote-debugging-port=52082 "
        "--user-data-dir=/tmp/DrissionPage/autoPortData/52082 --load-extension=/x/turnstilePatch"
    )
    related = [
        (28307, 1, main_cmd),
        (
            99999,
            28307,
            "chrome Helper --type=gpu --remote-debugging-port=52082 "
            "--user-data-dir=/tmp/DrissionPage/autoPortData/52082",
        ),
        (
            88888,
            1,
            "chrome Helper --type=renderer --user-data-dir=/tmp/DrissionPage/autoPortData/52082",
        ),
        (
            77777,
            1,
            "chrome --remote-debugging-port=11111 "
            "--user-data-dir=/tmp/DrissionPage/autoPortData/11111 --load-extension=/x/turnstilePatch",
        ),
    ]
    family = _family_pids_for_main(28307, main_cmd, related)
    assert 28307 in family
    assert 99999 in family
    assert 88888 in family
    assert 77777 not in family
    print("PASS family_pids_include_helpers")


def test_cleanup_selects_self_children_and_helpers() -> None:
    """dry_run with mocked ps: init orphan + untracked self-child + helper."""
    import sys

    sys.path.insert(0, str(ROOT))
    import tab_pool

    self_pid = 4242
    ps = f"""
 10001     1 chrome --remote-debugging-port=11111 --user-data-dir=/tmp/DrissionPage/autoPortData/11111 --load-extension=/x/turnstilePatch
 10002  {self_pid} chrome --remote-debugging-port=22222 --user-data-dir=/tmp/DrissionPage/autoPortData/22222 --load-extension=/x/turnstilePatch
 10003 10002 chrome Helper --type=gpu --remote-debugging-port=22222 --user-data-dir=/tmp/DrissionPage/autoPortData/22222
 10004  9999 chrome --remote-debugging-port=33333 --user-data-dir=/tmp/DrissionPage/autoPortData/33333 --load-extension=/x/turnstilePatch
 20000     1 chrome --remote-debugging-port=9222 --user-data-dir=/Users/me/chrome-coinbot-profile
"""
    with mock.patch.object(tab_pool, "_ps_ax_pid_ppid_cmd", return_value=ps), mock.patch.object(
        tab_pool.os, "getpid", return_value=self_pid
    ), mock.patch.object(tab_pool.os, "getppid", return_value=1):
        # only init orphans → self-child skipped
        res_init = tab_pool.cleanup_orphan_drission_chromes(
            only_ppid_init=True,
            include_self_children=False,
            kill_related_helpers=True,
            dry_run=True,
        )
        assert res_init["pids"] == [10001]

        # include self children → orphan + failed boot child
        res_self = tab_pool.cleanup_orphan_drission_chromes(
            only_ppid_init=True,
            include_self_children=True,
            kill_related_helpers=True,
            dry_run=True,
        )
        assert set(res_self["pids"]) == {10001, 10002}
        # live other-python child left alone
        assert 10004 not in res_self["pids"]
        # coinbot never matched
        assert 20000 not in res_self["pids"]

        # protect tracked live browser
        res_prot = tab_pool.cleanup_orphan_drission_chromes(
            only_ppid_init=True,
            include_self_children=True,
            protect_pids={10002},
            kill_related_helpers=True,
            dry_run=True,
        )
        assert res_prot["pids"] == [10001]
        assert res_prot["protected_skipped"] >= 1
    print("PASS cleanup_selects_self_children_and_helpers")


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
    assert "helpers_killed" in res
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


def test_call_sites_use_self_children() -> None:
    """Recycle / boot-fail / startup paths must request include_self_children."""
    ttk = (ROOT / "grok_register_ttk.py").read_text(encoding="utf-8")
    cli = (ROOT / "register_cli.py").read_text(encoding="utf-8")
    assert "include_self_children=True" in ttk
    assert "kill_related_helpers=True" in ttk
    assert "include_self_children=True" in cli
    assert "kill_related_helpers=True" in cli
    assert "helpers_killed" in cli
    print("PASS call_sites_use_self_children")


def test_autoport_min_age_floor() -> None:
    """tmp_dir_max_age_sec=0 must still floor to _AUTOPORT_MIN_AGE_SEC.

    DrissionPage creates autoPortData/<port> before Chrome execs; a sibling
    worker mid-boot has a dir that no process references in `ps` yet. Without
    the floor, concurrent cleanup would rmtree a live profile.
    """
    import sys
    import tempfile
    import time
    from pathlib import Path as P

    sys.path.insert(0, str(ROOT))
    import tab_pool

    assert tab_pool._AUTOPORT_MIN_AGE_SEC >= 30.0

    with tempfile.TemporaryDirectory() as td:
        fresh = P(td) / "fresh"
        stale = P(td) / "stale"
        fresh.mkdir()
        stale.mkdir()
        # Make stale old enough to pass any reasonable floor.
        old = time.time() - 3600
        import os

        os.utime(stale, (old, old))
        # fresh keeps mtime≈now

        with mock.patch.object(tab_pool, "_AUTOPORT_GLOB", str(P(td) / "*")):
            # Caller asks for 0 ("clean everything"); floor must still protect fresh.
            n = tab_pool._cleanup_stale_autoport_dirs(
                ps_text="",  # neither dir referenced by any process
                protect_udds=set(),
                log=lambda _m: None,
                dry_run=True,
                max_age_sec=0.0,
                errors=[],
            )
            # Only stale should be counted; fresh is younger than the floor.
            assert n == 1, f"expected only stale dir, got n={n}"
    print("PASS autoport_min_age_floor")


def main() -> int:
    test_is_drission_chrome_cmdline()
    test_parse_ps_chrome_rows_filters_ppid()
    test_family_pids_include_helpers()
    test_cleanup_selects_self_children_and_helpers()
    test_cleanup_dry_run_only_ppid_init()
    test_is_xvfb_and_parse()
    test_cleanup_orphan_xvfb_dry_run()
    test_call_sites_use_self_children()
    test_autoport_min_age_floor()
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
