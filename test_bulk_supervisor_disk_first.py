#!/usr/bin/env python3
"""Static gate: Grok bulk supervisor is intentional disk-first authority.

Bulk path = scripts/launch_batch_supervisor.sh → register_cli (concurrent chunk).
Must NOT route through register_core serial shell-out; must freeze:
  CPA_EXPORT_ENABLED=true / CPA_PROBE_CHAT=false / CPA_REMOTE_INJECT=false
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUP = ROOT / "scripts" / "launch_batch_supervisor.sh"


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


if __name__ == "__main__":
    unittest.main()
