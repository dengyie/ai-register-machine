"""control_api mail pool routes/ops (mocked refresh, no network)."""

from __future__ import annotations

import json

from pathlib import Path

import pytest

from apps.control_api import mail_ops


def _write_pool(path: Path, n: int = 5, domain: str = "hotmail.com") -> None:
    lines = [f"u{i}@{domain}----pw{i}----cid{i}----rt{i}\n" for i in range(n)]
    path.write_text("".join(lines), encoding="utf-8")


def test_get_pool_stats(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOTMAIL_ACCOUNTS_FILE", raising=False)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    pool = tmp_path / "mail_credentials.txt"
    _write_pool(pool, 3)
    with pool.open("a", encoding="utf-8") as f:
        f.write("z@outlook.com----p----c----r\n")

    st = mail_ops.get_pool_stats(tmp_path)
    assert st["total"] == 4
    assert st["by_domain"]["hotmail.com"] == 3
    assert st["by_domain"]["outlook.com"] == 1
    assert "known_domains" in st
    assert "quarantinable_statuses" in st
    blob = str(st)
    assert "pw0" not in blob
    assert "rt0" not in blob


def test_live_path_rejects_escape(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOTMAIL_ACCOUNTS_FILE", raising=False)
    # relative escape via config
    (tmp_path / "config.json").write_text(
        '{"hotmail_accounts_file": "../../../etc/passwd"}',
        encoding="utf-8",
    )
    path = mail_ops._live_path(tmp_path)
    assert path == (tmp_path.resolve() / "mail_credentials.txt")


def test_probe_mail_uses_injectable_via_core(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOTMAIL_ACCOUNTS_FILE", raising=False)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    pool = tmp_path / "mail_credentials.txt"
    _write_pool(pool, 6)

    def fake_refresh(acc):
        if acc.email.startswith("u0") or acc.email.startswith("u2"):
            return True, "", None
        if acc.email.startswith("u1"):
            return False, "Connection timed out", None
        return False, "AADSTS700082: grant is expired", None

    import mail_pool_probe as core

    monkeypatch.setattr(core, "_default_refresh", fake_refresh)

    out = mail_ops.probe_mail(
        tmp_path,
        domains=["hotmail.com"],
        limit=4,
        seed=1,
        concurrency=2,
    )
    assert out["probed"] == 4
    assert out["ok"] + out["dead"] == 4
    assert "quarantinable" in out
    assert out.get("mode") == "sample"
    assert out.get("offset") is None
    for row in out["results"]:
        assert set(row.keys()) == {
            "email",
            "domain",
            "status",
            "reason",
            "ms_error",
            "quarantinable",
        }
        assert "pw" not in row["email"]
        if row["status"] == "network_error":
            assert row["quarantinable"] is False
        if row["status"] == "grant_expired":
            assert row["quarantinable"] is True


def test_probe_mail_sequential_offset(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOTMAIL_ACCOUNTS_FILE", raising=False)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    pool = tmp_path / "mail_credentials.txt"
    _write_pool(pool, 5)

    import mail_pool_probe as core

    monkeypatch.setattr(core, "_default_refresh", lambda acc: (True, "", None))

    w0 = mail_ops.probe_mail(
        tmp_path,
        domains=["hotmail.com"],
        limit=2,
        offset=0,
        concurrency=2,
    )
    assert w0["mode"] == "sequential"
    assert w0["offset"] == 0
    assert w0["next_offset"] == 2
    assert w0["done"] is False
    assert w0["pool_filtered_total"] == 5
    assert [r["email"] for r in w0["results"]] == [
        "u0@hotmail.com",
        "u1@hotmail.com",
    ]

    w1 = mail_ops.probe_mail(
        tmp_path,
        domains=["hotmail.com"],
        limit=2,
        offset=w0["next_offset"],
        concurrency=2,
    )
    assert w1["offset"] == 2
    assert w1["next_offset"] == 4
    assert w1["done"] is False

    w2 = mail_ops.probe_mail(
        tmp_path,
        domains=["hotmail.com"],
        limit=2,
        offset=w1["next_offset"],
        concurrency=2,
    )
    assert w2["offset"] == 4
    assert w2["next_offset"] == 5
    assert w2["done"] is True
    assert w2["probed"] == 1


def test_quarantine_mail(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOTMAIL_ACCOUNTS_FILE", raising=False)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    pool = tmp_path / "mail_credentials.txt"
    _write_pool(pool, 4)

    with pytest.raises(ValueError):
        mail_ops.quarantine_mail(tmp_path, [])

    out = mail_ops.quarantine_mail(
        tmp_path,
        ["u1@hotmail.com", "u3@hotmail.com"],
        reason="probe:test",
    )
    assert out["removed"] == 2
    assert out["live_total_after"] == 2
    assert Path(out["backup_path"]).is_file()
    remaining = pool.read_text(encoding="utf-8")
    assert "u1@hotmail.com" not in remaining
    assert "u0@hotmail.com" in remaining
    dead = Path(out["dead_path"]).read_text(encoding="utf-8")
    assert "u1@hotmail.com----pw1----cid1----rt1----probe:test----" in dead


def test_compact_mail(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOTMAIL_ACCOUNTS_FILE", raising=False)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    pool = tmp_path / "mail_credentials.txt"
    pool.write_text(
        "u0@hotmail.com----pw0----cid0----rt0\n"
        "U0@hotmail.com----pwX----cidX----rtX\n"
        "u1@hotmail.com----pw1----cid1----rt1\n"
        "junk-line\n",
        encoding="utf-8",
    )
    st = mail_ops.get_pool_stats(tmp_path)
    assert st["total"] == 2
    assert st["duplicate_extra"] == 1
    assert st["invalid_lines"] == 1
    assert st["needs_compact"] is True

    preview = mail_ops.compact_mail(tmp_path, dry_run=True)
    assert preview["dry_run"] is True
    assert preview["duplicate_extra"] == 1
    assert "junk-line" in pool.read_text(encoding="utf-8")

    out = mail_ops.compact_mail(tmp_path, dry_run=False)
    assert out["changed"] is True
    assert out["unique"] == 2
    assert out["invalid_dropped"] == 1
    text = pool.read_text(encoding="utf-8")
    assert "u0@hotmail.com----pw0----cid0----rt0" in text
    assert "u1@hotmail.com----pw1----cid1----rt1" in text
    assert "pwX" not in text
    assert "junk-line" not in text
    # public result: emails ok, secrets must not appear
    blob = str(out)
    assert "pw0" not in blob
    assert "cid0" not in blob
    assert "rt0" not in blob


def test_routes_http_errors(tmp_path: Path, monkeypatch):
    """Route layer maps missing pool → 404, empty emails → 400."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from apps.control_api.auth import require_auth
    from apps.control_api import routes_mail
    from apps.control_api import settings as settings_mod

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOTMAIL_ACCOUNTS_FILE", raising=False)
    monkeypatch.delenv("REGISTER_PROJECT_ROOT", raising=False)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    class _S:
        project_root = tmp_path

    # routes_mail does `from ...settings import get_settings` — must patch the
    # bound name on the routes module, not only settings.get_settings.
    # Also clear lru_cache so a prior config TestClient cannot pin another root.
    settings_mod.clear_settings_cache()
    monkeypatch.setattr(settings_mod, "get_settings", lambda: _S())
    monkeypatch.setattr(routes_mail, "get_settings", lambda: _S())

    app = FastAPI()

    async def _ok():
        return True

    app.dependency_overrides[require_auth] = _ok
    app.include_router(routes_mail.router, dependencies=[])
    client = TestClient(app)

    r = client.get("/api/mail/pool")
    # empty/missing pool still returns stats with total=0
    assert r.status_code == 200
    assert r.json()["total"] == 0

    r = client.post("/api/mail/probe", json={"limit": 5, "domains": ["hotmail.com"]})
    assert r.status_code == 404

    r = client.post("/api/mail/quarantine", json={"emails": []})
    assert r.status_code == 422  # pydantic min_length

    _write_pool(tmp_path / "mail_credentials.txt", 2)
    r = client.post(
        "/api/mail/quarantine",
        json={"emails": ["   "], "reason": "x"},
    )
    # stripped empty → 400 from handler
    assert r.status_code == 400

    # compact endpoint
    with (tmp_path / "mail_credentials.txt").open("a", encoding="utf-8") as f:
        f.write("u0@hotmail.com----dup----dup----dup\n")
    r = client.post("/api/mail/compact", json={"dry_run": True})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["duplicate_extra"] >= 1
    assert "summary" in body
    r = client.post("/api/mail/compact", json={"dry_run": False})
    assert r.status_code == 200
    assert r.json()["unique"] == 2

    # sequential offset probe (injectable refresh, no network)
    import mail_pool_probe as core

    monkeypatch.setattr(core, "_default_refresh", lambda acc: (True, "", None))
    r = client.post(
        "/api/mail/probe",
        json={
            "domains": ["hotmail.com"],
            "limit": 1,
            "offset": 0,
            "concurrency": 1,
            "wall_seconds": 30,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "sequential"
    assert body["offset"] == 0
    assert body["next_offset"] == 1
    assert body["done"] is False
    assert body["pool_filtered_total"] == 2
    assert body["probed"] == 1
    # validation: limit > 500 / negative offset rejected by pydantic
    r = client.post("/api/mail/probe", json={"limit": 501, "offset": 0})
    assert r.status_code == 422
    r = client.post("/api/mail/probe", json={"limit": 1, "offset": -1})
    assert r.status_code == 422


def test_list_cloudflare_domains_requires_base(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("CLOUDFLARE_API_BASE", raising=False)
    monkeypatch.delenv("CLOUDFLARE_API_KEY", raising=False)
    try:
        mail_ops.list_cloudflare_domains(tmp_path)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "cloudflare_api_base" in str(exc)


def test_list_cloudflare_domains_parses_and_redacts(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(
        '{"cloudflare_api_base":"https://mail.example","cloudflare_api_key":"sekret1234","defaultDomains":"a.com"}',
        encoding="utf-8",
    )

    class _Resp:
        status = 200

        def read(self):
            return b'{"results":[{"domain":"a.com"},"b.com",{"name":"c.com"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=15):
        assert str(req.full_url).startswith("https://mail.example/domains")
        # Browser UA required to avoid CF Error 1010
        ua = req.headers.get("User-agent") or req.headers.get("User-Agent") or ""
        if not ua and hasattr(req, "get_header"):
            ua = req.get_header("User-agent") or req.get_header("User-Agent") or ""
        assert "Mozilla" in str(ua)
        return _Resp()

    monkeypatch.setattr(mail_ops, "urlopen", _fake_urlopen)
    out = mail_ops.list_cloudflare_domains(tmp_path)
    assert out["domains"] == ["a.com", "b.com", "c.com"]
    assert out["selected"] == ["a.com"]
    assert out["has_api_key"] is True
    assert out["path"] == "/domains"
    blob = str(out)
    assert "sekret1234" not in blob


def test_list_cloudflare_domains_falls_back_open_settings(tmp_path: Path, monkeypatch):
    """cloudflare_temp_email: /api/domains needs address JWT → 401; open_api/settings is public."""
    from urllib.error import HTTPError
    from io import BytesIO

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "cloudflare_api_base": "https://temp-mail.example",
                "cloudflare_path_domains": "/api/domains",
                "cloudflare_auth_mode": "none",
                "defaultDomains": "keep.me",
            }
        ),
        encoding="utf-8",
    )

    class _Ok:
        status = 200

        def read(self):
            return (
                b'{"title":"CF","domains":["mangoqwq.com","mangoq.ccwu.cc"],'
                b'"defaultDomains":["mangoqwq.com"]}'
            )

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls: list[str] = []

    def _fake_urlopen(req, timeout=15):
        url = str(req.full_url)
        calls.append(url)
        ua = req.headers.get("User-agent") or req.headers.get("User-Agent") or ""
        assert "Mozilla" in str(ua)
        if url.endswith("/api/domains"):
            raise HTTPError(
                url,
                401,
                "Unauthorized",
                hdrs=None,  # type: ignore[arg-type]
                fp=BytesIO(b"Invalid address credential"),
            )
        if url.endswith("/open_api/settings"):
            # Public path: no Authorization
            auth = req.headers.get("Authorization") or req.headers.get("authorization")
            assert not auth
            return _Ok()
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(mail_ops, "urlopen", _fake_urlopen)
    out = mail_ops.list_cloudflare_domains(tmp_path)
    assert out["domains"] == ["mangoqwq.com", "mangoq.ccwu.cc"]
    assert out["path"] == "/open_api/settings"
    assert out["selected"] == ["keep.me"]
    assert out["count"] == 2
    assert any(u.endswith("/api/domains") for u in calls)
    assert any(u.endswith("/open_api/settings") for u in calls)
