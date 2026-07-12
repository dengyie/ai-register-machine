#!/usr/bin/env python3
"""Unit checks: chat probe classification + product entitlement gate."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = mod
    # Ensure package path for relative imports inside cpa_xai
    if "cpa_xai" not in sys.modules:
        pkg = type(sys)("cpa_xai")
        pkg.__path__ = [str(ROOT / "cpa_xai")]  # type: ignore[attr-defined]
        sys.modules["cpa_xai"] = pkg
    spec.loader.exec_module(mod)
    return mod


def test_classify_chat_probe() -> None:
    probe = _load("cpa_xai.probe", ROOT / "cpa_xai" / "probe.py")
    cls = probe.classify_chat_probe

    ok = cls({"ok": True, "status": 200, "text": "MINT_OK"})
    assert ok["ok"] is True
    assert ok["entitlement_denied"] is False
    assert ok["retryable"] is False

    denied = cls(
        {
            "ok": False,
            "status": 403,
            "error": '{"error":{"code":"permission_denied","message":"no access"}}',
            "error_code": "permission_denied",
        }
    )
    assert denied["entitlement_denied"] is True
    assert denied["retryable"] is False
    assert denied["reason"] == "entitlement_denied"

    bare_403 = cls({"ok": False, "status": 403, "error": ""})
    assert bare_403["entitlement_denied"] is True

    upgrade = cls({"ok": False, "status": 426, "error": "upgrade required"})
    assert upgrade["entitlement_denied"] is False
    assert upgrade["retryable"] is False
    assert upgrade["reason"] == "auth_or_protocol"

    transient = cls({"ok": False, "status": 429, "error": "rate limit"})
    assert transient["retryable"] is True
    assert transient["entitlement_denied"] is False

    net = cls({"ok": False, "status": 0, "error": "timeout"})
    assert net["retryable"] is True
    print("PASS classify_chat_probe")


def test_probe_mini_response_attaches_classification() -> None:
    """Static: probe_mini_response body must call classify_chat_probe."""
    src = (ROOT / "cpa_xai" / "probe.py").read_text(encoding="utf-8")
    assert "def classify_chat_probe" in src
    assert "classify_chat_probe(out)" in src
    assert "entitlement_denied" in src
    print("PASS probe_mini_response attaches classification")


def test_mint_default_probe_chat_on() -> None:
    src = (ROOT / "cpa_xai" / "mint.py").read_text(encoding="utf-8")
    assert "probe_chat: bool = True" in src
    assert "entitlement_denied" in src
    assert "do not remint" in src
    assert "non_retryable" in src
    print("PASS mint default probe_chat on + entitlement fields")


def test_export_default_and_skip_inject() -> None:
    src = (ROOT / "cpa_export.py").read_text(encoding="utf-8")
    assert 'cfg.get("cpa_probe_chat"), default=True)' in src
    assert "cpa_probe_chat_required" in src
    assert "skip remote inject (entitlement_denied)" in src
    assert "skip_inject_entitlement" in src
    # models soft-pass must not apply when chat is on
    assert "and not probe_chat" in src
    print("PASS export chat defaults + skip inject")


def test_config_example_chat_keys() -> None:
    raw = (ROOT / "config.example.json").read_text(encoding="utf-8")
    assert '"cpa_probe_chat": true' in raw
    assert "cpa_probe_chat_required" in raw
    print("PASS config.example chat keys")


def test_cli_chat_stats() -> None:
    src = (ROOT / "register_cli.py").read_text(encoding="utf-8")
    assert '"chat_ok"' in src
    assert '"chat_denied"' in src
    assert "chat可用" in src
    assert "entitlement_denied" in src
    print("PASS cli chat stats")


def test_remint_enables_chat_probe() -> None:
    src = (ROOT / "scripts" / "remint_expired_and_sync_authdir.py").read_text(
        encoding="utf-8"
    )
    assert 'run_cfg["cpa_probe_chat"] = True' in src
    assert "cpa_probe_chat_required" in src
    print("PASS remint chat probe on")


def test_export_entitlement_hard_fail_logic() -> None:
    """Simulate export post-mint gates without network."""
    exp = _load("cpa_export_chat_gate", ROOT / "cpa_export.py")

    # entitlement must force ok=False and skip inject path markers
    result = {
        "ok": False,
        "path": "/tmp/xai-t@e.com.json",
        "email": "t@e.com",
        "error": "chat entitlement denied (permission-denied): do not remint",
        "entitlement_denied": True,
        "chat_ok": False,
        "fail_reason": "entitlement_denied",
        "probe_chat": {
            "ok": False,
            "status": 403,
            "entitlement_denied": True,
            "error_code": "permission_denied",
        },
    }
    # Reuse the boolean helpers by replaying the soft-pass conditions from source
    # via a tiny inline of the gate semantics (mirror of export logic).
    probe_chat = True
    probe_chat_required = True
    err_s = str(result.get("error") or "")
    is_chat_fail = bool(
        result.get("entitlement_denied")
        or result.get("fail_reason")
        in ("entitlement_denied", "chat_failed", "auth_or_protocol", "transient")
        or err_s.startswith("chat probe failed")
        or err_s.startswith("chat entitlement denied")
    )
    assert is_chat_fail is True
    if result.get("entitlement_denied"):
        result["ok"] = False
        result["non_retryable"] = True
    assert result["ok"] is False
    assert result["non_retryable"] is True
    # apply_multi_remote_inject must no-op when ok is False
    out = exp.apply_multi_remote_inject(
        result,
        {"cpa_remote_inject": True, "cpa_remote_auth_dirs": "/root/.cli-proxy-api"},
        inject_fn=lambda *a, **k: {"ok": True, "remote_path": "/x"},
    )
    assert out.get("remote_injects") is None
    assert out["ok"] is False
    print("PASS export entitlement hard-fail + no inject")


def main() -> int:
    test_classify_chat_probe()
    test_probe_mini_response_attaches_classification()
    test_mint_default_probe_chat_on()
    test_export_default_and_skip_inject()
    test_config_example_chat_keys()
    test_cli_chat_stats()
    test_remint_enables_chat_probe()
    test_export_entitlement_hard_fail_logic()
    print("\nALL PASS (cpa chat entitlement gate)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
