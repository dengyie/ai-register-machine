#!/usr/bin/env python3
"""Static + filesystem checks for project-local account backup."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def test_module_api() -> None:
    import account_backup as ab

    assert hasattr(ab, "snapshot_registered_accounts")
    assert hasattr(ab, "backup_after_success")
    print("PASS module api")


def test_snapshot_roundtrip() -> None:
    import account_backup as ab

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "cpa_auths").mkdir()
        (root / "accounts_cli.txt").write_text(
            "a@x.com----pw----" + ("s" * 40) + "\n"
            "b@x.com----pw2----" + ("t" * 40) + "\n",
            encoding="utf-8",
        )
        (root / "emails_used.txt").write_text("a@x.com\nb@x.com\n", encoding="utf-8")
        (root / "cpa_auths" / "xai-a@x.com.json").write_text('{"email":"a@x.com"}\n', encoding="utf-8")
        (root / "cpa_auths" / "xai-b@x.com.json").write_text('{"email":"b@x.com"}\n', encoding="utf-8")

        res = ab.snapshot_registered_accounts(
            root,
            reason="test",
            email="a@x.com",
            make_timestamped=True,
            log_callback=lambda m: None,
        )
        assert res["ok"] is True
        assert res["account_count"] == 2
        assert res["cpa_count"] == 2
        latest = Path(res["latest"])
        assert (latest / "accounts_cli.txt").is_file()
        assert (latest / "cpa_auths" / "xai-a@x.com.json").is_file()
        man = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
        assert man["account_count"] == 2
        assert man["trigger_email"] == "a@x.com"
        assert man["accounts"][0]["has_sso"] is True
        stamped = Path(res["stamped"])
        assert stamped.is_dir()
        assert (stamped / "manifest.json").is_file()

        # incremental success path refreshes latest only
        res2 = ab.backup_after_success(
            "b@x.com",
            root=root,
            cpa_path=root / "cpa_auths" / "xai-b@x.com.json",
            log_callback=lambda m: None,
        )
        assert res2["ok"] is True
        assert res2["stamped"] == ""
        print("PASS snapshot roundtrip")


def test_cli_wires_backup() -> None:
    src = (ROOT / "register_cli.py").read_text(encoding="utf-8")
    assert "account_backup" in src
    assert "backup_after_success" in src
    assert "snapshot_registered_accounts" in src
    exp = (ROOT / "cpa_export.py").read_text(encoding="utf-8")
    assert "account_backup" in exp
    assert "local_backup" in exp
    print("PASS cli/export wire backup")


def test_gitignore_covers_backups() -> None:
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "backups/" in gi
    assert "accounts_cli.txt" in gi
    assert "cpa_auths" in gi
    print("PASS gitignore covers backups")


def main() -> int:
    test_module_api()
    test_snapshot_roundtrip()
    test_cli_wires_backup()
    test_gitignore_covers_backups()
    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
