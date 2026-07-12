#!/usr/bin/env python3
"""Contract checks for outsider-friendly simple packaging."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_simple_config_template() -> None:
    p = ROOT / "config.simple.example.json"
    assert p.is_file(), "missing config.simple.example.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    cfg = {
        k: v
        for k, v in raw.items()
        if not (isinstance(k, str) and (k.startswith("//") or k.startswith("#")))
    }
    assert cfg.get("cpa_export_enabled") is True
    assert cfg.get("cpa_probe_chat") is True
    assert cfg.get("cpa_probe_chat_required") is True
    assert cfg.get("cpa_remote_inject") is False
    assert cfg.get("cpa_auth_dir") == "./cpa_auths"
    assert "cli-chat-proxy.grok.com" in str(cfg.get("cpa_base_url") or "")
    print("PASS simple config template")


def test_setup_script_and_readme() -> None:
    setup = ROOT / "scripts" / "setup_simple.sh"
    assert setup.is_file()
    src = setup.read_text(encoding="utf-8")
    assert "config.simple.example.json" in src
    assert "register_cli.py" in src
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "5 分钟快速开始" in readme
    assert "config.simple.example.json" in readme
    assert "entitlement_denied" in readme
    assert "setup_simple.sh" in readme
    print("PASS setup script + readme quickstart")


def test_gitignore_keeps_examples() -> None:
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    # secrets ignored, examples not
    assert "config.json" in gi or "config.json" in gi.splitlines()
    assert "config.simple.example.json" not in [
        ln.strip().lstrip("/") for ln in gi.splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]
    print("PASS gitignore does not ban simple example")


def main() -> int:
    test_simple_config_template()
    test_setup_script_and_readme()
    test_gitignore_keeps_examples()
    print("\nALL PASS (simple packaging)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
