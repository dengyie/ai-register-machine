"""Accounts pool + ops selfcheck/cleanup for control plane."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.control_api.accounts_ops import delete_account, list_accounts
from apps.control_api.system_ops import cleanup_orphans, selfcheck


def _write_auth(d: Path, email: str, *, access: bool = True, refresh: bool = True) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"xai-{email}.json"
    data = {
        "email": email,
        "access_token": "at-xxx" if access else "",
        "refresh_token": "rt-yyy" if refresh else "",
        "priority": 1000,
        "auth_kind": "sso",
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _client(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("REGISTER_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONTROL_API_SESSION_SECRET", "test-session-secret-32bytes-min!!")
    monkeypatch.setenv("CONTROL_API_PASSWORD_LOGIN", "1")
    monkeypatch.setenv("CONTROL_API_TOKEN", "secret-token")
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    from apps.control_api.settings import clear_settings_cache
    from apps.control_api.app import create_app
    from fastapi.testclient import TestClient

    clear_settings_cache()
    return TestClient(create_app())


def test_list_accounts_filter_and_paginate(tmp_path: Path):
    auths = tmp_path / "cpa_auths"
    _write_auth(auths, "a@ex.com", access=True, refresh=True)
    _write_auth(auths, "b@ex.com", access=True, refresh=False)
    _write_auth(auths, "c@other.com", access=True, refresh=True)

    all_items = list_accounts(tmp_path, page_size=0)
    assert all_items["total"] == 3
    assert all_items["disk_complete"] == 2
    assert all_items["complete"] == 2

    only_ok = list_accounts(tmp_path, complete="1", page_size=50)
    assert only_ok["total"] == 2
    assert all(it["complete"] for it in only_ok["items"])

    only_bad = list_accounts(tmp_path, complete="0", page_size=50)
    assert only_bad["total"] == 1
    assert only_bad["items"][0]["email"] == "b@ex.com"

    q = list_accounts(tmp_path, q="other", page_size=50)
    assert q["total"] == 1
    assert q["items"][0]["email"] == "c@other.com"

    page1 = list_accounts(tmp_path, page=1, page_size=2)
    assert page1["page"] == 1
    assert page1["page_size"] == 2
    assert page1["pages"] == 2
    assert len(page1["items"]) == 2


def test_delete_account_safe_name(tmp_path: Path):
    auths = tmp_path / "cpa_auths"
    path = _write_auth(auths, "del@ex.com")
    assert path.is_file()
    out = delete_account(tmp_path, "xai-del@ex.com.json")
    assert out["ok"] is True
    assert not path.exists()
    # Soft-delete: archived under cpa_auths/.trash/
    assert out.get("archived")
    archived = Path(out["archived_path"])
    assert archived.is_file()
    assert archived.parent.name == ".trash"
    assert archived.parent.parent == auths

    with pytest.raises(FileNotFoundError):
        delete_account(tmp_path, "xai-missing@ex.com.json")

    with pytest.raises(ValueError, match="invalid|must be"):
        delete_account(tmp_path, "../evil.json")

    with pytest.raises(ValueError, match="must be"):
        delete_account(tmp_path, "not-xai.json")


def test_list_accounts_summary_cache_skips_reread(tmp_path: Path, monkeypatch):
    auths = tmp_path / "cpa_auths"
    path = _write_auth(auths, "cache@ex.com")
    reads = {"n": 0}
    real_read = Path.read_text

    def counting_read(self, *a, **k):
        if self == path:
            reads["n"] += 1
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", counting_read)
    list_accounts(tmp_path, page_size=50)
    list_accounts(tmp_path, page_size=50)
    # Second list should hit mtime cache, not re-read file body.
    assert reads["n"] == 1


def test_selfcheck_missing_and_present(tmp_path: Path):
    out = selfcheck(tmp_path)
    assert "checks" in out
    assert "ok" in out
    names = {c["name"] for c in out["checks"]}
    assert "config.json" in names
    assert "cpa_auths" in names
    assert "register_cli.py" in names

    (tmp_path / "config.json").write_text(
        json.dumps({"email_provider": "cloudflare", "defaultDomains": "a.com"}),
        encoding="utf-8",
    )
    (tmp_path / "register_cli.py").write_text("# stub\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "launch_batch_supervisor.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    _write_auth(tmp_path / "cpa_auths", "ok@ex.com")

    out2 = selfcheck(tmp_path)
    by = {c["name"]: c for c in out2["checks"]}
    assert by["config.json"]["ok"] is True
    assert by["cpa_auths"]["ok"] is True
    assert by["register_cli.py"]["ok"] is True
    assert by["email_config"]["ok"] is True
    assert by["venv_python"]["ok"] is True


def test_cleanup_orphans_success_path():
    chrome = {"killed": 2, "matched": 2}
    xvfb = {"killed": 1, "matched": 1}
    fake = SimpleNamespace(
        cleanup_orphan_drission_chromes=lambda **_k: chrome,
        cleanup_orphan_xvfb=lambda **_k: xvfb,
    )
    with mock.patch.dict(sys.modules, {"tab_pool": fake}):
        out = cleanup_orphans(dry_run=False)
    assert out["ok"] is True
    assert out["chrome"]["killed"] == 2
    assert out["xvfb"]["killed"] == 1
    assert "chrome_killed=2" in out["detail"]


def test_cleanup_orphans_dry_run():
    chrome = {"would_kill": 3, "matched": 3}
    xvfb = {"would_kill": 1, "matched": 1}
    fake = SimpleNamespace(
        cleanup_orphan_drission_chromes=lambda **_k: chrome,
        cleanup_orphan_xvfb=lambda **_k: xvfb,
    )
    with mock.patch.dict(sys.modules, {"tab_pool": fake}):
        out = cleanup_orphans(dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert "chrome_would=3" in out["detail"]


def test_routes_accounts_and_ops(tmp_path: Path, monkeypatch):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    _write_auth(tmp_path / "cpa_auths", "route@ex.com")
    (tmp_path / "register_cli.py").write_text("#\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "launch_batch_supervisor.sh").write_text("#!/bin/bash\n", encoding="utf-8")

    client = _client(tmp_path, monkeypatch)
    headers = {"Authorization": "Bearer secret-token"}

    r = client.get("/api/accounts", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert any(it["email"] == "route@ex.com" for it in body["items"])

    sc = client.get("/api/ops/selfcheck", headers=headers)
    assert sc.status_code == 200
    assert "checks" in sc.json()

    name = "xai-route@ex.com.json"
    d = client.delete(f"/api/accounts/{name}", headers=headers)
    assert d.status_code == 200
    assert d.json()["ok"] is True

    d2 = client.delete(f"/api/accounts/{name}", headers=headers)
    assert d2.status_code == 404

    # invalid account name rejected by API
    bad = client.delete("/api/accounts/not-xai.json", headers=headers)
    assert bad.status_code == 400

    fake = SimpleNamespace(
        cleanup_orphan_drission_chromes=lambda **_k: {"killed": 0, "matched": 0},
        cleanup_orphan_xvfb=lambda **_k: {"killed": 0, "matched": 0},
    )
    with mock.patch.dict(sys.modules, {"tab_pool": fake}):
        cl = client.post("/api/ops/cleanup-orphans?dry_run=true", headers=headers)
    assert cl.status_code == 200
    assert cl.json()["ok"] is True
