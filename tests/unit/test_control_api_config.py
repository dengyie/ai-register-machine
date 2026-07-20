"""Config IO and overview count tests."""

from __future__ import annotations

import json
from pathlib import Path

from apps.control_api.config_io import load_config, redact_config, save_config
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


def test_save_preserves_masked_secret(tmp_path: Path):
    (tmp_path / "config.json").write_text(
        json.dumps({"cloudflare_api_key": "real-secret-value"}),
        encoding="utf-8",
    )
    save_config(tmp_path, {"cloudflare_api_key": "***alue", "email_provider": "gmail"})
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert data["cloudflare_api_key"] == "real-secret-value"
    assert data["email_provider"] == "gmail"


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
