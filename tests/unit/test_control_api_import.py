"""Import ops tests."""

from __future__ import annotations

import zipfile
from pathlib import Path
from unittest import mock

import pytest

from apps.control_api.imports_ops import (
    ensure_under,
    import_mail,
    import_nodes,
    import_pack,
    save_upload,
)


def test_path_traversal_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="escapes"):
        ensure_under(tmp_path, tmp_path / ".." / "etc" / "passwd")


def test_save_upload_size_cap(tmp_path: Path):
    with pytest.raises(ValueError, match="max_upload"):
        save_upload(tmp_path, "big.bin", b"x" * 100, max_bytes=10)


def test_mail_append_backup(tmp_path: Path):
    target = tmp_path / "mail_credentials.txt"
    target.write_text("old@x.com----a----b----c\n", encoding="utf-8")
    result = import_mail(tmp_path, "new@x.com----a----b----c\n", mode="append")
    assert result["ok"] is True
    assert result["backup"]
    text = target.read_text(encoding="utf-8")
    assert "old@x.com" in text
    assert "new@x.com" in text


def test_mail_replace(tmp_path: Path):
    target = tmp_path / "mail_credentials.txt"
    target.write_text("old\n", encoding="utf-8")
    import_mail(tmp_path, "only-new\n", mode="replace")
    assert target.read_text(encoding="utf-8") == "only-new\n"


def test_nodes_dry_run_invokes_script(tmp_path: Path):
    script = tmp_path / "scripts" / "import_nodes.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    f = tmp_path / "output" / "web_uploads" / "n.yaml"
    f.parent.mkdir(parents=True)
    f.write_text("proxies: []\n", encoding="utf-8")

    class R:
        returncode = 0
        stdout = "imported 0"
        stderr = ""

    with mock.patch("apps.control_api.imports_ops.subprocess.run", return_value=R()) as run:
        result = import_nodes(tmp_path, f, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    argv = run.call_args[0][0]
    assert "--dry-run" in argv


def test_import_pack_plan_and_apply(tmp_path: Path):
    zpath = tmp_path / "pack.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("config.json", '{"email_provider":"cloudflare"}')
        zf.writestr("nodes.json", "[]")
    # move zip under staging-friendly path
    staging = tmp_path / "output" / "web_uploads"
    staging.mkdir(parents=True)
    z2 = staging / "pack.zip"
    z2.write_bytes(zpath.read_bytes())
    plan = import_pack(tmp_path, z2, apply=False)
    assert plan["plan"]["config"]
    assert plan["applied"] == {}
    applied = import_pack(tmp_path, z2, apply=True)
    assert (tmp_path / "config.json").is_file()
    assert "config" in applied["applied"]
