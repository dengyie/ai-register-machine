"""Config IO and overview count tests."""

from __future__ import annotations

import json
from pathlib import Path

from apps.control_api.config_io import (
    config_to_env_map,
    enrich_config_from_env,
    load_config,
    redact_config,
    save_config,
    sync_config_to_env,
    update_env_file,
)
from apps.control_api.overview import count_product_ok


def test_load_strips_comment_keys(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "// note": "x",
                "email_provider": "cloudflare",
                "cloudflare_api_key": "abc12345",
            }
        ),
        encoding="utf-8",
    )
    data = load_config(tmp_path)
    assert "// note" not in data
    assert data["email_provider"] == "cloudflare"


def test_redact_masks_secrets():
    out = redact_config(
        {
            "email_provider": "cloudflare",
            "cloudflare_api_key": "abc12345",
            "proxy": "http://x",
        }
    )
    assert out["email_provider"] == "cloudflare"
    assert out["cloudflare_api_key"].startswith("***")
    assert "2345" in out["cloudflare_api_key"]
    assert out["proxy"] == "http://x"


def test_save_backup_and_preserve_secret_on_empty(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "email_provider": "cloudflare",
                "cloudflare_api_key": "keepme-secret",
                "defaultDomains": "a.com",
            }
        ),
        encoding="utf-8",
    )
    # Pre-seed .env with unrelated operational key that must survive a partial save.
    (tmp_path / ".env").write_text("PROXY=http://127.0.0.1:7897\n", encoding="utf-8")
    result = save_config(
        tmp_path,
        {
            "email_provider": "cloudflare",
            "cloudflare_api_key": "",
            "defaultDomains": "b.com",
        },
    )
    assert result["backup"]
    assert Path(result["backup"]).is_file()
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert data["cloudflare_api_key"] == "keepme-secret"
    assert data["defaultDomains"] == "b.com"
    # only payload operational keys mirrored; PROXY untouched
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "EMAIL_PROVIDER=cloudflare" in env_text
    assert "DEFAULT_DOMAINS=b.com" in env_text
    assert "PROXY=http://127.0.0.1:7897" in env_text
    # secrets must never land in .env via console sync
    assert "keepme-secret" not in env_text
    assert "cloudflare_api_key" not in env_text.lower()


def test_save_preserves_masked_secret(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"cloudflare_api_key": "real-secret-value"}),
        encoding="utf-8",
    )
    save_config(tmp_path, {"cloudflare_api_key": "***alue", "email_provider": "gmail"})
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert data["cloudflare_api_key"] == "real-secret-value"
    assert data["email_provider"] == "gmail"


def test_config_to_env_map_formats_lists_and_bools():
    m = config_to_env_map(
        {
            "email_provider": "hotmail",
            "email_providers": ["duckmail", "gmail"],
            "cpa_probe_chat": False,
            "mail_timeout": 20,
            "defaultDomains": "a.com,b.com",
            "cloudflare_api_key": "secret-should-skip",  # secret key substr
        }
    )
    assert m["EMAIL_PROVIDER"] == "hotmail"
    assert m["EMAIL_PROVIDERS"] == "duckmail,gmail"
    assert m["CPA_PROBE_CHAT"] == "false"
    assert m["MAIL_TIMEOUT"] == "20"
    assert m["DEFAULT_DOMAINS"] == "a.com,b.com"
    assert "CLOUDFLARE_API_KEY" not in m


def test_update_env_file_preserves_comments_and_unknown(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "# keep me\n"
        "EMAIL_PROVIDER=cloudflare\n"
        "CUSTOM_UNKNOWN=stay\n"
        "MAIL_TIMEOUT=10\n",
        encoding="utf-8",
    )
    out = update_env_file(
        env,
        {
            "EMAIL_PROVIDER": "hotmail",
            "EMAIL_PROVIDERS": "",
            "EVIL_NOT_ALLOWED": "nope",
        },
    )
    assert "EMAIL_PROVIDER" in out["changed_env_keys"]
    text = env.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "EMAIL_PROVIDER=hotmail" in text
    assert "CUSTOM_UNKNOWN=stay" in text
    assert "EVIL_NOT_ALLOWED" not in text
    assert "EMAIL_PROVIDERS=" in text
    assert out["backup"]
    assert Path(out["backup"]).is_file()


def test_sync_config_to_env_from_disk(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "email_provider": "hotmail",
                "email_provider_strategy": "failover",
                "cloudflare_api_base": "https://temp-mail.example",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("EMAIL_PROVIDER=cloudflare\n", encoding="utf-8")
    out = sync_config_to_env(tmp_path)
    assert "EMAIL_PROVIDER" in out["changed_env_keys"]
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "EMAIL_PROVIDER=hotmail" in text
    assert "EMAIL_PROVIDER_STRATEGY=failover" in text
    assert "CLOUDFLARE_API_BASE=https://temp-mail.example" in text


def test_count_product_ok(tmp_path: Path):
    d = tmp_path / "cpa_auths"
    d.mkdir()
    (d / "xai-a.json").write_text(
        json.dumps({"access_token": "a", "refresh_token": "r"}), encoding="utf-8"
    )
    (d / "xai-b.json").write_text(json.dumps({"access_token": "a"}), encoding="utf-8")
    (d / "other.json").write_text(
        json.dumps({"access_token": "a", "refresh_token": "r"}), encoding="utf-8"
    )
    assert count_product_ok(tmp_path) == 1


def test_save_sticky_empty_proxy_does_not_wipe_env(tmp_path: Path):
    """Register page used to POST proxy=\"\" and blank host PROXY / DEFAULT_DOMAINS."""
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "email_provider": "cloudflare",
                "proxy": "http://127.0.0.1:7897",
                "defaultDomains": "a.com,b.com",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "EMAIL_PROVIDER=cloudflare\n"
        "PROXY=http://127.0.0.1:7897\n"
        "DEFAULT_DOMAINS=a.com,b.com\n",
        encoding="utf-8",
    )
    result = save_config(
        tmp_path,
        {
            "email_provider": "hotmail",
            "proxy": "",
            "defaultDomains": "   ",
            "proxy_list": "",  # clearable
        },
    )
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert data["email_provider"] == "hotmail"
    assert data["proxy"] == "http://127.0.0.1:7897"
    assert data["defaultDomains"] == "a.com,b.com"
    assert data.get("proxy_list") == ""
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "EMAIL_PROVIDER=hotmail" in env_text
    assert "PROXY=http://127.0.0.1:7897" in env_text
    assert "DEFAULT_DOMAINS=a.com,b.com" in env_text
    assert "PROXY_LIST=" in env_text
    assert "EMAIL_PROVIDER" in result["changed_env_keys"]
    assert "PROXY" not in result["changed_env_keys"]
    assert "DEFAULT_DOMAINS" not in result["changed_env_keys"]


def test_config_to_env_map_sticky_empty_skips_proxy():
    m = config_to_env_map(
        {
            "email_provider": "hotmail",
            "proxy": "",
            "defaultDomains": "",
            "email_providers": [],
        }
    )
    assert m["EMAIL_PROVIDER"] == "hotmail"
    assert "PROXY" not in m
    assert "DEFAULT_DOMAINS" not in m
    # multi-select may clear
    assert m.get("EMAIL_PROVIDERS") == ""


def test_enrich_config_from_env_fills_blank_ops(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"email_provider": "hotmail", "proxy": "", "defaultDomains": ""}),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "PROXY=http://127.0.0.1:7897\n"
        "DEFAULT_DOMAINS=mangoqwq.com,a.cd\n"
        "EMAIL_PROVIDER=cloudflare\n",  # config non-blank wins
        encoding="utf-8",
    )
    out = enrich_config_from_env(tmp_path)
    assert out["email_provider"] == "hotmail"
    assert out["proxy"] == "http://127.0.0.1:7897"
    assert out["defaultDomains"] == "mangoqwq.com,a.cd"








def test_config_api_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REGISTER_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONTROL_API_TOKEN", "t")
    (tmp_path / "config.json").write_text(
        json.dumps({"email_provider": "cloudflare", "defaultDomains": "a.com"}),
        encoding="utf-8",
    )
    from apps.control_api.app import create_app
    from apps.control_api.settings import clear_settings_cache
    from fastapi.testclient import TestClient

    clear_settings_cache()
    client = TestClient(create_app())
    headers = {"Authorization": "Bearer t"}
    r = client.get("/api/config", headers=headers)
    assert r.status_code == 200
    assert r.json()["config"]["email_provider"] == "cloudflare"
    r2 = client.put(
        "/api/config",
        headers=headers,
        json={"config": {"email_provider": "gmail", "defaultDomains": "b.com"}},
    )
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert "defaultDomains" in r2.json()["changed_keys"]
    body = r2.json()
    assert "EMAIL_PROVIDER" in body.get("changed_env_keys", [])
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "EMAIL_PROVIDER=gmail" in env_text
    assert "DEFAULT_DOMAINS=b.com" in env_text


def test_config_api_get_enriches_from_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REGISTER_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONTROL_API_TOKEN", "t")
    (tmp_path / "config.json").write_text(
        json.dumps({"email_provider": "hotmail", "proxy": "", "defaultDomains": ""}),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "PROXY=http://127.0.0.1:7897\nDEFAULT_DOMAINS=keep.me\n",
        encoding="utf-8",
    )
    from apps.control_api.app import create_app
    from apps.control_api.settings import clear_settings_cache
    from fastapi.testclient import TestClient

    clear_settings_cache()
    client = TestClient(create_app())
    headers = {"Authorization": "Bearer t"}
    r = client.get("/api/config", headers=headers)
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["email_provider"] == "hotmail"
    assert cfg["proxy"] == "http://127.0.0.1:7897"
    assert cfg["defaultDomains"] == "keep.me"
    # disk unchanged
    disk = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert disk["proxy"] == ""
    assert disk["defaultDomains"] == ""


def test_config_api_put_sticky_empty_and_enrich(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REGISTER_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("CONTROL_API_TOKEN", "t")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "email_provider": "cloudflare",
                "proxy": "http://old:1",
                "defaultDomains": "old.com",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "PROXY=http://old:1\nDEFAULT_DOMAINS=old.com\nEMAIL_PROVIDER=cloudflare\n",
        encoding="utf-8",
    )
    from apps.control_api.app import create_app
    from apps.control_api.settings import clear_settings_cache
    from fastapi.testclient import TestClient

    clear_settings_cache()
    client = TestClient(create_app())
    headers = {"Authorization": "Bearer t"}
    r = client.put(
        "/api/config",
        headers=headers,
        json={
            "config": {
                "email_provider": "hotmail",
                "proxy": "",
                "defaultDomains": "",
            }
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["config"]["email_provider"] == "hotmail"
    assert body["config"]["proxy"] == "http://old:1"
    assert body["config"]["defaultDomains"] == "old.com"
    assert "PROXY" not in body.get("changed_env_keys", [])
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "PROXY=http://old:1" in env_text
    assert "DEFAULT_DOMAINS=old.com" in env_text
    assert "EMAIL_PROVIDER=hotmail" in env_text




