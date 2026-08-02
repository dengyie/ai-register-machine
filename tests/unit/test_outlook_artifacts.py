"""Unit tests for Outlook private artifact sink + pure provider helpers (Task 9).

Browser-free and network-free. All values are synthetic. The private sink file
legitimately contains the password and refresh_token (that is its purpose);
the public RegisterResult view must never leak them.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from register_core.providers.outlook_adapter import OutlookProvider

CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"


def _provider(tmp_path: Path) -> OutlookProvider:
    return OutlookProvider(config={"outlook_auths_dir": str(tmp_path / "auths")})


def test_write_outlook_artifact_creates_0600_file(tmp_path):
    provider = _provider(tmp_path)
    created = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
    path = provider._write_outlook_artifact(
        email="user@outlook.com",
        password="synthetic-password",
        client_id=CLIENT_ID,
        refresh_token="synthetic-refresh",
        recovery_email="r@invalid",
        bound=True,
        created_at=created,
    )
    assert Path(path).exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    assert body == {
        "email": "user@outlook.com",
        "password": "synthetic-password",
        "client_id": CLIENT_ID,
        "refresh_token": "synthetic-refresh",
        "recovery_email": "r@invalid",
        "bound": True,
        "created_at": "20260722T120000Z",
    }


def test_filename_sanitizes_email_and_is_collision_safe(tmp_path):
    provider = _provider(tmp_path)
    stamp = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)

    p1 = provider._write_outlook_artifact(
        email="User@Outlook.com", password="p", client_id="c",
        refresh_token="r", recovery_email="", bound=False, created_at=stamp,
    )
    assert Path(p1).name == "outlook-user-outlook-com-20260722T120000Z.json"

    # Same email + same second → no clobber; a numeric suffix is appended.
    p2 = provider._write_outlook_artifact(
        email="user@outlook.com", password="p", client_id="c",
        refresh_token="r", recovery_email="", bound=False, created_at=stamp,
    )
    assert Path(p2).name == "outlook-user-outlook-com-20260722T120000Z-1.json"
    assert len({p1, p2}) == 2

    # Different email → distinct file, no collision-suffix.
    p3 = provider._write_outlook_artifact(
        email="other@outlook.com", password="p", client_id="c",
        refresh_token="r", recovery_email="", bound=False, created_at=stamp,
    )
    assert Path(p3).name == "outlook-other-outlook-com-20260722T120000Z.json"
    assert len({p1, p2, p3}) == 3


def test_write_outlook_artifact_atomic_no_temp_left_behind(tmp_path):
    provider = _provider(tmp_path)
    provider._write_outlook_artifact(
        email="a@outlook.com", password="p", client_id="c",
        refresh_token="r", recovery_email="", bound=False,
        created_at=datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc),
    )
    leftovers = list(Path(tmp_path / "auths").glob(".outlook-*.tmp"))
    assert leftovers == []


def test_write_outlook_artifact_tightens_auths_dir_to_0700(tmp_path):
    """The auths directory is tightened to 0700 to match the 0600-file secret
    discipline; a default umask would otherwise leave it world-readable."""
    provider = _provider(tmp_path)
    provider._write_outlook_artifact(
        email="dir@outlook.com", password="p", client_id="c",
        refresh_token="r", recovery_email="", bound=False,
        created_at=datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc),
    )
    auths_dir = tmp_path / "auths"
    assert stat.S_IMODE(os.stat(auths_dir).st_mode) == 0o700
    # The file itself remains 0600.
    f = next(auths_dir.glob("outlook-dir-outlook-com-*.json"))
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_generate_account_email_is_random_and_suffixed():
    provider = OutlookProvider()
    a = provider._generate_account_email("@outlook.com")
    b = provider._generate_account_email("@outlook.com")
    assert a.endswith("@outlook.com")
    assert a.startswith("orx")
    local = a.split("@", 1)[0]
    assert len(local) == 15 and all(c in "0123456789abcdef" for c in local[3:])
    assert a != b


def test_generate_password_is_url_safe_and_unique():
    provider = OutlookProvider()
    p = provider._generate_password()
    assert isinstance(p, str) and len(p) >= 18
    assert not any(c.isspace() for c in p)
    assert p == p.strip()
    assert provider._generate_password() != p


def test_failure_helper_normalizes_kind_and_carries_no_secret():
    provider = OutlookProvider()
    # The pass-through kind "captcha" normalizes to itself; the raw detail text
    # is what the caller cares about, not the kind.
    result = provider._failure(
        "captcha", "fun captcha detail", {"outlook_steps": ["register"]}
    )
    assert result.ok is False
    assert result.provider == "outlook"
    assert result.secret_kind == "none"
    assert result.secret == ""
    assert result.error_kind == "captcha"
    assert result.artifacts == {"outlook_steps": ["register"]}
    # A transport-shaped alias maps through normalize_error_kind as documented.
    proxied = provider._failure("missing_attempt_proxy", "x")
    assert proxied.error_kind == "proxy"
