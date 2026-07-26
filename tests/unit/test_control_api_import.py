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
    assert result["status"] == "success"
    assert result["new"] == 1
    assert result["duplicate"] == 0
    assert "导入成功" in result["summary"]
    assert "new@x.com" in result["summary"]
    text = target.read_text(encoding="utf-8")
    assert "old@x.com" in text
    assert "new@x.com" in text


def test_mail_replace(tmp_path: Path):
    target = tmp_path / "mail_credentials.txt"
    target.write_text("keep@x.com----a----b----c\n", encoding="utf-8")
    result = import_mail(tmp_path, "new@x.com----p----c----r\n", mode="replace")
    assert result["ok"] is True
    assert result["lines_written"] == 1
    assert target.read_text(encoding="utf-8") == "new@x.com----p----c----r\n"


def test_mail_replace_all_invalid_refused(tmp_path: Path):
    """replace with 0 valid credentials must not truncate the pool."""
    target = tmp_path / "mail_credentials.txt"
    target.write_text("keep@x.com----a----b----c\n", encoding="utf-8")
    with pytest.raises(ValueError, match="将清空邮箱池"):
        import_mail(tmp_path, "only-new\n", mode="replace")
    # pool untouched, and no stray backup written
    assert target.read_text(encoding="utf-8") == "keep@x.com----a----b----c\n"
    assert not list(tmp_path.glob("mail_credentials.txt.bak-web-*"))


def test_mail_replace_empty_pool_allowed(tmp_path: Path):
    """No existing pool → replace with invalid input is a harmless no-op, not an error."""
    result = import_mail(tmp_path, "only-new\n", mode="replace")
    assert result["ok"] is True
    assert result["lines_written"] == 0
    assert result["status"] == "empty"
    assert "未导入" in result["summary"]


def test_mail_import_json_object_normalized(tmp_path: Path):
    target = tmp_path / "mail_credentials.txt"
    target.write_text("keep@x.com----a----b----c\n", encoding="utf-8")
    payload = (
        '{"email":"Kanode@outlook.com","password":"p1",'
        '"clientId":"cid-1","refreshToken":"rt-1"}'
    )
    result = import_mail(tmp_path, payload, mode="append")
    assert result["ok"] is True
    assert result["lines_written"] == 1
    assert result["new"] == 1
    assert result["status"] == "success"
    assert result["normalized"]["json_objects"] == 1
    assert result["formats"]["json_objects"] == 1
    assert "JSON 1" in result["summary"]
    text = target.read_text(encoding="utf-8")
    assert "keep@x.com----a----b----c" in text
    assert "Kanode@outlook.com----p1----cid-1----rt-1" in text
    assert '{"email"' not in text


def test_mail_import_json_array(tmp_path: Path):
    target = tmp_path / "mail_credentials.txt"
    payload = (
        '[{"email":"a@outlook.com","password":"pa","clientId":"c1","refreshToken":"r1"},'
        '{"email":"bad","password":"x","clientId":"c","refreshToken":"r"},'
        '{"email":"b@hotmail.com","password":"pb","clientId":"c2","refreshToken":"r2"}]'
    )
    result = import_mail(tmp_path, payload, mode="replace")
    assert result["lines_written"] == 2
    assert result["skipped"] == 1
    assert result["status"] == "partial"
    assert "部分成功" in result["summary"]
    assert result["normalized"]["skipped"] == 1
    lines = [ln for ln in target.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines == [
        "a@outlook.com----pa----c1----r1",
        "b@hotmail.com----pb----c2----r2",
    ]


def test_mail_import_pipe_and_csv(tmp_path: Path):
    target = tmp_path / "mail_credentials.txt"
    result = import_mail(tmp_path, "p@outlook.com|pw|cid-pipe1|rt-pipe001\n", mode="replace")
    assert result["lines_written"] == 1
    assert "p@outlook.com----pw----cid-pipe1----rt-pipe001" in target.read_text(encoding="utf-8")

    csv_only = (
        "email,password,client_id,refresh_token\n"
        "r@live.com,pr,cid-csv2,rt-csv002\n"
    )
    result2 = import_mail(tmp_path, csv_only, mode="replace")
    assert result2["lines_written"] == 1
    assert result2["normalized"].get("csv_rows") == 1
    assert "r@live.com----pr----cid-csv2----rt-csv002" in target.read_text(encoding="utf-8")


def test_mail_import_append_dedupe_feedback(tmp_path: Path):
    """Append skips emails already in pool and reports duplicate honestly."""
    target = tmp_path / "mail_credentials.txt"
    target.write_text(
        "exist@outlook.com----pw----cid-exist----rt-exist\n",
        encoding="utf-8",
    )
    payload = (
        "exist@outlook.com----pw2----cid2----rt2\n"
        "fresh@outlook.com----pw3----cid3----rt3\n"
        "EXIST@outlook.com----pw4----cid4----rt4\n"  # case-insensitive dup
        "fresh@outlook.com----pw5----cid5----rt5\n"  # batch-internal dup
    )
    result = import_mail(tmp_path, payload, mode="append")
    assert result["ok"] is True
    assert result["new"] == 1
    assert result["lines_written"] == 1
    assert result["duplicate"] == 3
    assert result["status"] == "partial"
    assert result["pool_before"] == 1
    assert result["pool_after"] == 2
    assert "部分成功" in result["summary"]
    assert "新增 1" in result["summary"]
    assert "重复跳过 3" in result["summary"]
    assert "fresh@outlook.com" in result["new_emails"]
    assert "exist@outlook.com" in [e.lower() for e in result["duplicate_emails"]]
    text = target.read_text(encoding="utf-8")
    assert text.count("fresh@outlook.com") == 1
    assert text.count("exist@outlook.com") == 1
    # only original exist line + one new
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 2


def test_mail_import_all_duplicate_feedback(tmp_path: Path):
    target = tmp_path / "mail_credentials.txt"
    target.write_text("a@outlook.com----p----c----r\n", encoding="utf-8")
    result = import_mail(
        tmp_path,
        "a@outlook.com----p2----c2----r2\n",
        mode="append",
    )
    assert result["new"] == 0
    assert result["duplicate"] == 1
    assert result["status"] == "empty"
    assert result["backup"] is None  # nothing written → no bak
    assert "未新增" in result["summary"]
    assert "全部已在池中" in result["summary"]
    assert target.read_text(encoding="utf-8") == "a@outlook.com----p----c----r\n"


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
