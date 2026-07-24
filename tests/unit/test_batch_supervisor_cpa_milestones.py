"""Static contracts for disk-first batch CPA import milestones."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUP = ROOT / "scripts" / "launch_batch_supervisor.sh"


def test_supervisor_forces_per_account_inject_off():
    src = SUP.read_text(encoding="utf-8")
    assert "export CPA_REMOTE_INJECT=false" in src
    # Intent may be true, but mint path freezes inject off.
    assert "per_account_inject=false" in src or "per_account_inject=false" in src.replace(" ", "")


def test_supervisor_milestone_import_defaults_100():
    src = SUP.read_text(encoding="utf-8")
    assert "CPA_BATCH_IMPORT_EVERY:-100" in src or 'CPA_BATCH_IMPORT_EVERY:-100' in src
    assert "CPA_BATCH_IMPORT_SIZE:-100" in src
    assert "maybe_milestone_cpa_import" in src
    assert "LAST_IMPORT_COMPLETE" in src


def test_supervisor_import_only_on_gained_and_remainder():
    src = SUP.read_text(encoding="utf-8")
    # Mid-loop: only after gained > 0, not every sub.
    assert "if (( gained > 0 )); then" in src
    assert 'maybe_milestone_cpa_import "$AFTER_COMPLETE"' in src
    # Final remainder path exists; no unconditional import every account.
    assert "target_reached_remainder" in src or "remainder" in src
    # Must not call import inside the register_cli command block.
    mint_block = src.split("python -u register_cli.py", 1)[1].split("code=$?", 1)[0]
    assert "run_batch_end_cpa_import" not in mint_block
    assert "import_cpa_auth_dir.py" not in mint_block


def test_supervisor_watermark_soft_fail_advances():
    """Import soft-failure must still advance LAST_IMPORT_COMPLETE (no spin)."""
    src = SUP.read_text(encoding="utf-8")
    assert "Advance watermark even on soft failure" in src
    assert "LAST_IMPORT_COMPLETE=$cur_complete" in src
    # Milestone path returns 0 after soft import (never stalls mint loop).
    assert "(soft)" in src
    # Probe always hard-false on mint path.
    assert "export CPA_PROBE_CHAT=false" in src


def test_supervisor_residential_proxy_rotate_off():
    """Residential must not hardcode --proxy-rotate clash (CLI overrides env)."""
    src = SUP.read_text(encoding="utf-8")
    # Shared launch uses mode variable, not a literal clash flag.
    assert '--proxy-rotate "$PROXY_ROTATE_CLI"' in src
    assert "PROXY_ROTATE_CLI=off" in src
    # Must not hardcode clash on the register_cli line (the residential bug).
    mint_block = src.split("python -u register_cli.py", 1)[1].split("code=$?", 1)[0]
    assert "--proxy-rotate clash" not in mint_block
    assert '--proxy-rotate "$PROXY_ROTATE_CLI"' in mint_block
    # Residential branch sets rotate off + 1024 PROXY.
    assert "mode=residential" in src
    assert "PROXY_ROTATE_MODE=off" in src
