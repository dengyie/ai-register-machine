"""Supervisor-side marker/glob contract tests for Outlook (Task 11).

Live-only behaviour is not exercised. These tests pin the invariants the
supervisor shell script must keep:

1. The stdout completion-marker grep covers the same set the existing
   xai/mimo/chatgpt runs emit (SUMMARY_JSON, === 完成, Fatal, FAIL-FAST, 注册成功).
2. Outlook artifacts are counted with a ``-name 'outlook-*.json'`` find that is
   disjoint from the ``xai-*.json`` CPA import glob; the two globs never share a
   directory or a pattern.
3. The python-level completion inventory at the bottom of the script keeps
   iterating ``xai-*.json`` over ``cpa_auths`` (unchanged), so Outlook artifacts
   are NOT accidentally imported into the CPA pool.
"""

from __future__ import annotations

import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "launch_batch_supervisor.sh"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_supervisor_declares_disjoint_outlook_artifact_branch(tmp_path):
    # Globs are genuinely disjoint in shell `find` semantics.
    (tmp_path / "outlook-a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "xai-a.json").write_text("{}", encoding="utf-8")
    assert [p.name for p in tmp_path.glob("outlook-*.json")] == ["outlook-a.json"]
    assert [p.name for p in tmp_path.glob("xai-*.json")] == ["xai-a.json"]

    script = _script()
    # Progress accounting recognizes a "注册成功" line (existing marker reused).
    assert "注册成功" in script
    # The Outlook count uses a strict find with the outlook-*.json glob.
    assert "-name 'outlook-*.json'" in script
    # The xai CPA inventory glob is preserved untouched (python glob form,
    # matched in detail in test_supervisor_cpa_inventory_still_glob_xai_only).
    assert 'Path("cpa_auths").glob("xai-*.json")' in script
    # Markers retained across products.
    for marker in ("SUMMARY_JSON", "=== 完成", "Fatal", "FAIL-FAST"):
        assert marker in script


def test_supervisor_outlook_count_is_separate_directory_from_cpa_auths():
    script = _script()
    # The Outlook count reads from the outlook_auths dir (env-overridable),
    # never from cpa_auths where xai-*.json lives.
    assert "outlook_auths" in script
    assert "OUTLOOK_AUTHS_DIR" in script
    assert "OUTLOOK_AUTH_COUNT" in script


def test_supervisor_cpa_inventory_still_glob_xai_only():
    """The python completion inventory must not be widened — Outlook artifacts
    are deliberately excluded from the CPA token audit so they don't get
    imported as grok/xai accounts."""
    script = _script()
    # Exactly the existing xai-*.json inventory block over cpa_auths remains.
    assert 'Path("cpa_auths").glob("xai-*.json")' in script
    # No accidental broadening to *.json or outlook-*.json inside that audit.
    audit_idx = script.index('Path("cpa_auths").glob("xai-*.json")')
    audit_window = script[audit_idx:audit_idx + 1200]
    assert "outlook" not in audit_window


def test_supervisor_script_shell_syntax_ok():
    """bash -n must pass (static syntax), so the new branch can't break parsing."""
    import subprocess

    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def test_outlook_summary_marker_contract(tmp_path):
    """A SUMMARY_JSON line the runner can emit for outlook round-trips to the
    same redacted public shape used by the control API (no secret leaking)."""
    from apps.control_api.routes_ops import _public_outlook_record

    summary = {
        "provider": "outlook",
        "ok": True,
        "success": 1,
        "fail": 0,
        "stopped_reason": "",
        "email": "user@outlook.com",
        "password": "synthetic-password",
        "client_id": "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        "refresh_token": "synthetic-refresh",
        "bound": True,
    }
    public = _public_outlook_record(
        {k: v for k, v in summary.items() if k != "stopped_reason"}
    )
    blob = json.dumps(public)
    assert "synthetic-password" not in blob
    assert "synthetic-refresh" not in blob
    assert public["email"] == "user@outlook.com"
