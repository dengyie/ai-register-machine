"""Outlook auth import/export helper tests (Task 10).

The control API surfaces Outlook accounts via a strict ``outlook-*.json`` glob
and a redacted public record. These helpers must never return a password or
refresh_token value in their public output, and must not match ``xai-*.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.control_api.routes_ops import (
    _outlook_auth_files,
    _public_outlook_record,
)


def _auth(email="u@outlook.com", password="p", client_id="c", refresh_token="r", **extra):
    payload = {
        "email": email,
        "password": password,
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    payload.update(extra)
    return payload


def test_outlook_auth_files_glob_excludes_xai(tmp_path):
    (tmp_path / "outlook-user.json").write_text(
        json.dumps(_auth()), encoding="utf-8"
    )
    (tmp_path / "xai-user.json").write_text(
        json.dumps({"email": "x@example.com"}), encoding="utf-8"
    )
    # Collision-suffixed files still match.
    (tmp_path / "outlook-user-20260722T120000Z-1.json").write_text(
        json.dumps(_auth()), encoding="utf-8"
    )
    # Non-json and dotfiles ignored.
    (tmp_path / "outlook-notes.txt").write_text("noise", encoding="utf-8")

    found = _outlook_auth_files(tmp_path)
    assert found == [
        tmp_path / "outlook-user-20260722T120000Z-1.json",
        tmp_path / "outlook-user.json",
    ]


def test_outlook_auth_files_missing_root_returns_empty(tmp_path):
    assert _outlook_auth_files(tmp_path / "does-not-exist") == []


def test_public_outlook_record_redacts_secrets():
    record = _public_outlook_record(
        _auth(password="real-pw", refresh_token="real-refresh", bound=True,
              recovery_email="r@invalid")
    )
    assert record == {
        "email": "u@outlook.com",
        "client_id": "c",
        "bound": True,
        "recovery_email": "r@invalid",
        "has_password": True,
        "has_refresh_token": True,
    }
    assert "real-pw" not in json.dumps(record)
    assert "real-refresh" not in json.dumps(record)


def test_public_outlook_record_reports_absent_secret_flags_as_true_when_present():
    # All four required fields present → both has_* flags True; bound defaults.
    record = _public_outlook_record(
        {"email": "u@outlook.com", "password": "p", "client_id": "c",
         "refresh_token": "r"}
    )
    assert record["has_password"] is True
    assert record["has_refresh_token"] is True
    assert record["bound"] is False


def test_public_outlook_record_rejects_missing_required_fields():
    # Per the contract, all four of email/password/client_id/refresh_token are
    # required; any missing (or empty) one raises ValueError.
    for missing in ("email", "password", "client_id", "refresh_token"):
        payload = _auth()
        payload[missing] = ""  # falsy counts as missing
        with pytest.raises(ValueError) as excinfo:
            _public_outlook_record(payload)
        assert missing in str(excinfo.value)


def test_public_outlook_record_roundtrips_through_real_sink_layout(tmp_path):
    """The artifact written by OutlookProvider._write_outlook_artifact must parse
    via the helper — guards the on-disk contract between sink and API."""
    from register_core.providers.outlook_adapter import OutlookProvider

    provider = OutlookProvider(config={"outlook_auths_dir": str(tmp_path)})
    from datetime import datetime, timezone

    provider._write_outlook_artifact(
        email="sink@outlook.com",
        password="sink-pw",
        client_id="9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        refresh_token="sink-refresh",
        recovery_email="r@invalid",
        bound=True,
        created_at=datetime(2026, 8, 3, 0, 0, 0, tzinfo=timezone.utc),
    )
    paths = _outlook_auth_files(tmp_path)
    assert len(paths) == 1
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert _public_outlook_record(payload)["email"] == "sink@outlook.com"


def test_api_list_outlook_accounts_redacts_and_skips_malformed(tmp_path, monkeypatch):
    """GET /api/outlook-accounts returns redacted records over outlook_auths/,
    never xai, skips malformed/partial files instead of 500ing, and never
    leaks password/refresh_token."""
    import json as _json
    from types import SimpleNamespace

    from apps.control_api import routes_ops

    auths = tmp_path / "outlook_auths"
    auths.mkdir()
    # Two good records + one malformed JSON + one partial (missing required).
    (auths / "outlook-good.json").write_text(
        _json.dumps(
            _auth(password="real-pw", refresh_token="real-refresh", bound=True,
                  recovery_email="r@invalid")
        ),
        encoding="utf-8",
    )
    (auths / "outlook-second.json").write_text(
        _json.dumps(_auth(email="b@outlook.com")), encoding="utf-8"
    )
    (auths / "outlook-broken.json").write_text("{not json", encoding="utf-8")
    (auths / "outlook-partial.json").write_text(
        _json.dumps({"email": "x@outlook.com"}), encoding="utf-8"  # missing 3 fields
    )
    # xai must NEVER be picked up here.
    (auths / "xai-leak.json").write_text(_json.dumps({"email": "x@x.ai"}), encoding="utf-8")

    # Point the route's settings lookup at the tmp root without relying on the
    # lru-cached get_settings (which reads the real process root).
    monkeypatch.setattr(
        routes_ops, "get_settings", lambda: SimpleNamespace(project_root=tmp_path)
    )

    resp = routes_ops.api_list_outlook_accounts()
    assert resp["count"] == 2
    assert resp["skipped"] == 2  # broken json + partial
    assert resp["dir"] == "outlook_auths"
    emails = sorted(r["email"] for r in resp["accounts"])
    assert emails == ["b@outlook.com", "u@outlook.com"]
    blob = _json.dumps(resp)
    assert "real-pw" not in blob
    assert "real-refresh" not in blob
    # xai accounts are excluded by the strict glob.
    assert "x@x.ai" not in blob


def test_api_list_outlook_accounts_honors_outlook_auths_dir_env(tmp_path, monkeypatch):
    """Listing must honor OUTLOOK_AUTHS_DIR so count/list/write all agree on the
    same directory the supervisor and adapter use (findings #8 alignment)."""
    import json as _json
    from types import SimpleNamespace

    from apps.control_api import routes_ops

    custom = tmp_path / "custom_outlook_auths"
    custom.mkdir()
    (custom / "outlook-env.json").write_text(
        _json.dumps(_auth(email="env@outlook.com")), encoding="utf-8"
    )
    # A sibling default-named dir stays untouched — confirms we read custom only.
    default = tmp_path / "outlook_auths"
    default.mkdir()
    (default / "outlook-default.json").write_text(
        _json.dumps(_auth(email="default@outlook.com")), encoding="utf-8"
    )

    monkeypatch.setattr(
        routes_ops, "get_settings", lambda: SimpleNamespace(project_root=tmp_path)
    )
    monkeypatch.setenv("OUTLOOK_AUTHS_DIR", "custom_outlook_auths")

    resp = routes_ops.api_list_outlook_accounts()
    assert resp["dir"] == "custom_outlook_auths"
    assert resp["count"] == 1
    assert resp["accounts"][0]["email"] == "env@outlook.com"
