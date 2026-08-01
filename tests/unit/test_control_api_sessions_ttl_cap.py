"""§#163 — session ttl hard cap: lifetime can only shrink, never grow.

The operator-tunable CONTROL_API_SESSION_TTL is clamped in get_settings to
[MIN, MAX]. mint_session_token AND read_session_token both bound the baked-in
ttl payload field to MAX_SESSION_TTL_SECONDS, so:

  - a runaway TTL env cannot mint an effectively-immortal cookie, and
  - a token minted by an older build that did NOT clamp the ttl (and so carries
    a ttl past MAX) is refused at read time even while still within max_age.
"""

from __future__ import annotations

import pytest

from apps.control_api.sessions import (
    mint_session_token,
    read_session_token,
    session_cookie_kwargs,
)
from apps.control_api.settings import (
    MAX_SESSION_TTL_SECONDS,
    MIN_SESSION_TTL_SECONDS,
)


SECRET = "test-session-secret-for-ttl-cap!!"


def test_settings_clamps_ttl_into_band(monkeypatch):
    """A TTL env above the cap is clamped down; below the floor is clamped up."""
    from apps.control_api.settings import clear_settings_cache, get_settings

    monkeypatch.setenv("CONTROL_API_SESSION_TTL", str(MAX_SESSION_TTL_SECONDS * 10))
    clear_settings_cache()
    s = get_settings()
    assert s.session_ttl_seconds == MAX_SESSION_TTL_SECONDS

    monkeypatch.setenv("CONTROL_API_SESSION_TTL", "60")  # below floor
    clear_settings_cache()
    s = get_settings()
    assert s.session_ttl_seconds == MIN_SESSION_TTL_SECONDS


def test_cookie_max_age_capped_at_constant():
    """session_cookie_kwargs never returns a max_age above the hard ceiling."""
    kw = session_cookie_kwargs(secure=False, max_age=MAX_SESSION_TTL_SECONDS * 5)
    assert kw["max_age"] == MAX_SESSION_TTL_SECONDS
    # a normal value passes through
    assert session_cookie_kwargs(secure=True, max_age=3600)["max_age"] == 3600


def test_mint_clamps_payload_ttl():
    """mint_session_token never bakes a ttl past the cap into the signed token."""
    token = mint_session_token(
        secret=SECRET, username="ops", ttl_seconds=MAX_SESSION_TTL_SECONDS * 10
    )
    payload = read_session_token(token, secret=SECRET, max_age=MAX_SESSION_TTL_SECONDS * 10)
    assert payload is not None
    assert payload["ttl"] == MAX_SESSION_TTL_SECONDS


def test_read_rejects_token_whose_baked_ttl_exceeds_cap():
    """A token crafted with ttl past MAX_SESSION_TTL_SECONDS is refused on read.

    Simulates an older build that minted without the clamp: we forge a payload
    carrying an oversized ttl through mint (bypassed by writing the payload
    directly) and confirm read_session_token rejects it via the max_ttl guard,
    independent of max_age being generous.
    """
    import time

    from apps.control_api.sessions import _serializer

    over_ttl = MAX_SESSION_TTL_SECONDS + 3600
    tampered = _serializer(SECRET).dumps(
        {"u": "ops", "iat": int(time.time()), "ttl": over_ttl}
    )
    # max_age is generous (well over the token's actual age) — refusal must come
    # from the baked ttl exceeding the ceiling, not from expiry.
    assert read_session_token(tampered, secret=SECRET, max_age=over_ttl * 2) is None


def test_read_accepts_normal_ttl():
    token = mint_session_token(secret=SECRET, username="ops", ttl_seconds=3600)
    payload = read_session_token(token, secret=SECRET, max_age=3600)
    assert payload is not None
    assert payload["u"] == "ops"
    assert payload["ttl"] == 3600


def test_negative_ttl_treated_as_invalid():
    """A signed token with a nonsensical ttl (<0) is rejected, not normalized."""
    import time

    from apps.control_api.sessions import _serializer

    token = _serializer(SECRET).dumps(
        {"u": "ops", "iat": int(time.time()), "ttl": -5}
    )
    assert read_session_token(token, secret=SECRET, max_age=3600) is None
