#!/usr/bin/env python3
"""Static gate: Grok bulk supervisor is intentional disk-first authority.

Bulk path = scripts/launch_batch_supervisor.sh → register_cli (concurrent chunk).
Must NOT route through register_core serial shell-out; must freeze:
  CPA_EXPORT_ENABLED=true / CPA_PROBE_CHAT=false / CPA_REMOTE_INJECT=false
during mint. Optional batch-end inject is a *post-target* unified import only.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUP = ROOT / "scripts" / "launch_batch_supervisor.sh"
IMPORT = ROOT / "scripts" / "import_cpa_auth_dir.py"


class TestBulkSupervisorDiskFirst(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(SUP.is_file(), f"missing {SUP}")
        self.src = SUP.read_text(encoding="utf-8")

    def test_disk_first_env_frozen(self) -> None:
        # Force product boundary: mint write only, no chat probe, no inject.
        self.assertIn("CPA_EXPORT_ENABLED=true", self.src)
        self.assertIn("CPA_PROBE_CHAT=false", self.src)
        self.assertIn("CPA_REMOTE_INJECT=false", self.src)
        # Criterion documented in script header/log
        self.assertIn("complete", self.src.lower())
        self.assertIn("refresh", self.src.lower())

    def test_calls_register_cli_not_register_core(self) -> None:
        # Concurrent bulk authority: direct register_cli with --extra chunk.
        self.assertIn("register_cli.py", self.src)
        self.assertIn("--extra", self.src)
        # Must not silently migrate bulk onto serial register_core.
        self.assertNotIn("register_core run", self.src)
        self.assertNotIn("run-register-core.sh", self.src)

    def test_success_criterion_is_disk_not_chat(self) -> None:
        # Log line / comment must state disk success, not chat_ok product.
        self.assertRegex(
            self.src,
            r"success criterion:.*complete xai-.*refresh",
        )
        self.assertIn("not chat_ok", self.src)

    def test_batch_end_import_only_after_target(self) -> None:
        """Inject intent ≠ per-account inject; only post-target unified import."""
        self.assertIn("BATCH_CPA_INJECT_INTENT", self.src)
        self.assertIn("run_batch_end_cpa_import", self.src)
        self.assertIn("import_cpa_auth_dir.py", self.src)
        self.assertIn("HIT_TARGET", self.src)
        # Mint loop must keep forcing inject false (not honor config mid-batch).
        self.assertIn("export CPA_REMOTE_INJECT=false", self.src)
        # Import gated on target reached + intent.
        self.assertIn("target_reached", self.src)
        self.assertIn('HIT_TARGET == 1', self.src)
        # Soft: import fail must not kill disk product.
        self.assertIn("soft", self.src.lower())
        # Batch-of-5 default for Clash-friendly import.
        self.assertIn("CPA_BATCH_IMPORT_SIZE", self.src)
        self.assertIn("--batch-size", self.src)

    def test_import_script_has_force_remote_flag(self) -> None:
        self.assertTrue(IMPORT.is_file(), f"missing {IMPORT}")
        src = IMPORT.read_text(encoding="utf-8")
        self.assertIn('--remote', src)
        self.assertIn("args.remote", src)
        # --remote must force inject even when config is false.
        self.assertIn("elif args.remote:", src)


if __name__ == "__main__":
    unittest.main()
