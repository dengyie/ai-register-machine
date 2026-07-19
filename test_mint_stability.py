#!/usr/bin/env python3
"""Gates for mint-stability milestone: flock, device stall taxonomy, xvfb cleanup, smoke script."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
SMOKE = ROOT / "scripts" / "smoke_diskfirst_one.sh"


class TestSmokeDiskfirstScript(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SMOKE.is_file(), f"missing {SMOKE}")
        self.src = SMOKE.read_text(encoding="utf-8")

    def test_disk_first_env_frozen(self) -> None:
        self.assertIn("CPA_EXPORT_ENABLED=true", self.src)
        self.assertIn("CPA_PROBE_CHAT=false", self.src)
        self.assertIn("CPA_REMOTE_INJECT=false", self.src)
        self.assertIn("flock", self.src)
        self.assertIn("register_cli.py", self.src)
        self.assertIn("complete", self.src.lower())
        self.assertIn("refresh", self.src.lower())
        self.assertIn("not chat_ok", self.src)

    def test_single_instance_lock(self) -> None:
        self.assertIn("grok_smoke_diskfirst.lock", self.src)
        self.assertIn("flock -n", self.src)


class TestRegisterCliLock(unittest.TestCase):
    def test_acquire_and_block_second_process(self) -> None:
        """flock is process-scoped; verify a child process is denied while held."""
        import subprocess
        import sys

        sys.path.insert(0, str(ROOT))
        import register_cli as rc

        with tempfile.TemporaryDirectory() as td:
            lock = str(Path(td) / "cli.lock")
            rc._release_cli_lock()
            ok1, msg1 = rc.acquire_register_cli_lock(lock_path=lock, skip=False)
            self.assertTrue(ok1, msg1)
            self.assertIn("acquired", msg1)
            # Child process must fail non-blocking exclusive lock.
            child = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import fcntl, os, sys\n"
                        f"p={lock!r}\n"
                        "fd=os.open(p, os.O_RDWR|os.O_CREAT, 0o644)\n"
                        "try:\n"
                        "    fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
                        "except BlockingIOError:\n"
                        "    sys.exit(2)\n"
                        "else:\n"
                        "    sys.exit(0)\n"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(child.returncode, 2, child.stdout + child.stderr)
            rc._release_cli_lock()
            ok3, msg3 = rc.acquire_register_cli_lock(lock_path=lock, skip=False)
            self.assertTrue(ok3, msg3)
            rc._release_cli_lock()

    def test_skip_env(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        import register_cli as rc

        rc._release_cli_lock()
        ok, msg = rc.acquire_register_cli_lock(skip=True)
        self.assertTrue(ok)
        self.assertIn("skipped", msg)

    def test_classify_mint_fail(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        import register_cli as rc

        r, p = rc._classify_mint_fail(
            {"error": "device_click_stall phase=device clicks=6 stall_sec=30.0"}
        )
        self.assertEqual(r, "device_click_stall")
        self.assertEqual(p, "device")

        r, p = rc._classify_mint_fail(
            {"error": "browser confirm timeout phase=device login_attempts=0"}
        )
        self.assertEqual(r, "browser_timeout")
        self.assertEqual(p, "device")

        r, p = rc._classify_mint_fail(
            {"mint_fail_reason": "auth_failed", "mint_fail_phase": "password"}
        )
        self.assertEqual(r, "auth_failed")
        self.assertEqual(p, "password")


class TestMintFailTaxonomy(unittest.TestCase):
    def test_classify_browser_mint_error(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        from cpa_xai.mint import classify_browser_mint_error

        r, p = classify_browser_mint_error(
            "device_click_stall phase=device clicks=6 stall_sec=22.1 budget=45"
        )
        self.assertEqual(r, "device_click_stall")
        self.assertEqual(p, "device")

        r, p = classify_browser_mint_error(
            "browser confirm timeout phase=consent login_attempts=0"
        )
        self.assertEqual(r, "browser_timeout")
        self.assertEqual(p, "consent")

        r, p = classify_browser_mint_error("auth failed: turnstile stuck")
        self.assertEqual(r, "auth_failed")

        r, p = classify_browser_mint_error("The browser connection fails")
        self.assertEqual(r, "browser_boot")

        r, p = classify_browser_mint_error("cancelled")
        self.assertEqual(r, "cancelled")


class TestDeviceClickStallSource(unittest.TestCase):
    def test_approve_device_code_has_early_abort(self) -> None:
        src = (ROOT / "cpa_xai" / "browser_confirm.py").read_text(encoding="utf-8")
        self.assertIn("device_click_stall", src)
        self.assertIn("device_stall_budget_sec", src)
        self.assertIn("device_stall_click_limit", src)


class TestXvfbCleanup(unittest.TestCase):
    def test_is_xvfb_cmdline(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        from tab_pool import is_xvfb_cmdline, parse_ps_xvfb_rows

        self.assertTrue(is_xvfb_cmdline("Xvfb :99 -screen 0 1280x900x24"))
        self.assertTrue(is_xvfb_cmdline("/usr/bin/Xvfb :100 -ac"))
        self.assertFalse(is_xvfb_cmdline("xvfb-run -a python register_cli.py"))
        self.assertFalse(is_xvfb_cmdline("/usr/bin/Xorg :0"))

        ps = """
 111 1 /usr/bin/Xvfb :99 -screen 0 1280x900x24
 222 50 /usr/bin/Xvfb :100 -ac
 333 1 xvfb-run -a python x
 444 1 /usr/bin/chrome --remote-debugging-port=1
"""
        rows = parse_ps_xvfb_rows(ps)
        pids = {r[0] for r in rows}
        self.assertEqual(pids, {111, 222})

    def test_cleanup_orphan_xvfb_dry_run(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT))
        from tab_pool import cleanup_orphan_xvfb

        logs: list[str] = []
        res = cleanup_orphan_xvfb(
            log_callback=logs.append,
            only_ppid_init=True,
            require_no_children=True,
            dry_run=True,
            clean_tmp_dirs=False,
        )
        self.assertIn("scanned", res)
        self.assertIn("matched", res)
        self.assertTrue(res.get("dry_run"))
        self.assertIsInstance(res.get("pids"), list)

    def test_register_cli_hooks_xvfb_cleanup(self) -> None:
        src = (ROOT / "register_cli.py").read_text(encoding="utf-8")
        self.assertIn("cleanup_orphan_xvfb", src)
        self.assertIn("acquire_register_cli_lock", src)
        self.assertIn("mint_fail_reason", src)


if __name__ == "__main__":
    unittest.main()
