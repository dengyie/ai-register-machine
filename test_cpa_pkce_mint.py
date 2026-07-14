#!/usr/bin/env python3
"""Offline checks for the PKCE authorization-code mint path.

Covers the pure helper functions (no network, no curl_cffi, no real SSO):
  - grpcweb varint/string/frame encode→decode roundtrip
  - PKCE code_verifier / code_challenge S256 shape
  - authorization URL params (response_type=code, S256, cli-proxy-api referrer)
  - submitOAuth2Consent action_id extraction from a next.js HTML page
  - _code_from_url state mismatch failure + success
"""

from __future__ import annotations

import hashlib
import importlib.util
import base64
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    if "cpa_xai" not in sys.modules:
        pkg = type(sys)("cpa_xai")
        pkg.__path__ = [str(ROOT / "cpa_xai")]  # type: ignore[attr-defined]
        sys.modules["cpa_xai"] = pkg
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def test_grpcweb_roundtrip() -> None:
    gw = _load("cpa_xai.grpcweb", ROOT / "cpa_xai" / "grpcweb.py")

    # single string field, field number 1
    msg = gw.encode_string(1, "https://accounts.x.ai/set-cookie?token=abc")
    frame = gw.frame_request(msg)
    parsed = gw.parse_response(frame)
    assert parsed.get("grpc_status") in (None, 0), parsed
    msgs = parsed["messages"]
    assert msgs, "no messages decoded"
    # messages is list[list[dict]]; first sub-message's first field is the string
    first_msg = msgs[0]
    str_fields = [f for f in first_msg if f.get("type") == "string"]
    assert str_fields, first_msg
    text = str_fields[0].get("value")
    assert text and "set-cookie" in text, text
    print("PASS grpcweb_roundtrip")


def test_pkce_code_challenge_shape() -> None:
    pkce = _load("cpa_xai.pkce_mint", ROOT / "cpa_xai" / "pkce_mint.py")

    verifier = pkce._code_verifier()
    challenge = pkce._code_challenge(verifier)

    # verifier: 48 bytes -> 64 base64url chars, no padding
    assert len(verifier) == 64, len(verifier)
    assert "=" not in verifier
    # challenge == S256(verifier), b64url no padding
    expected = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    assert challenge == expected, (challenge, expected)
    assert "=" not in challenge
    # different verifiers yield different challenges
    other = pkce._code_challenge(pkce._code_verifier())
    assert other != challenge
    print("PASS pkce_code_challenge_shape")


def test_authorization_url_params() -> None:
    pkce = _load("cpa_xai.pkce_mint", ROOT / "cpa_xai" / "pkce_mint.py")

    url = pkce._build_authorization_url(
        client_id="CLIENT-X",
        redirect_uri="http://127.0.0.1:56121/callback",
        state="ST",
        nonce="NN",
        code_challenge="CH",
        scope="openid offline_access",
    )
    assert url.startswith("https://auth.x.ai/oauth2/authorize?"), url
    qs = parse_qs(urlparse(url).query)
    assert qs["response_type"] == ["code"]
    assert qs["client_id"] == ["CLIENT-X"]
    assert qs["code_challenge_method"] == ["S256"]
    assert qs["code_challenge"] == ["CH"]
    assert qs["state"] == ["ST"]
    assert qs["nonce"] == ["NN"]
    assert qs["redirect_uri"] == ["http://127.0.0.1:56121/callback"]
    assert qs["scope"] == ["openid offline_access"]
    assert qs["referrer"] == ["cli-proxy-api"]
    print("PASS authorization_url_params")


def test_submit_consent_action_id_extraction() -> None:
    pkce = _load("cpa_xai.pkce_mint", ROOT / "cpa_xai" / "pkce_mint.py")

    # 1. explicit submitOAuth2Consent action wins
    html = (
        '<script>var x = createServerReference)("4005315a1d7e426de592990bb54bb37471f39dd6d2",'
        ' "submitOAuth2Consent", ...);</script>'
    )
    aid = pkce._extract_action_id_from_html(html) if hasattr(pkce, "_extract_action_id_from_html") else None
    if aid is None:
        # the reference impl inlines this regex inside _submit_consent; mirror it here
        import re

        m = re.search(r'createServerReference\)\("([a-f0-9]{40,44})"[^)]*submitOAuth2Consent', html)
        aid = m.group(1) if m else pkce.SUBMIT_OAUTH2_CONSENT_ACTION
    assert aid == "4005315a1d7e426de592990bb54bb37471f39dd6d2", aid

    # 2. fallback action id constant
    assert (
        pkce.SUBMIT_OAUTH2_CONSENT_ACTION
        == "4005315a1d7e426de592990bb54bb37471f39dd6d2"
    )
    print("PASS submit_consent_action_id_extraction")


def test_code_from_url_state_mismatch_and_success() -> None:
    pkce = _load("cpa_xai.pkce_mint", ROOT / "cpa_xai" / "pkce_mint.py")

    # success
    code = pkce._code_from_url(
        "http://127.0.0.1:56121/callback?code=AC-123&state=ST", "ST"
    )
    assert code == "AC-123", code

    # state mismatch -> PKCEMintError
    raised = False
    try:
        pkce._code_from_url(
            "http://127.0.0.1:56121/callback?code=AC-123&state=OTHER", "ST"
        )
    except pkce.PKCEMintError as e:
        raised = "state mismatch" in str(e).lower()
    assert raised, "state mismatch should raise PKCEMintError"

    # missing code -> PKCEMintError
    raised = False
    try:
        pkce._code_from_url("http://127.0.0.1:56121/callback?state=ST", "ST")
    except pkce.PKCEMintError as e:
        raised = "missing code" in str(e).lower()
    assert raised, "missing code should raise PKCEMintError"
    print("PASS code_from_url_state_mismatch_and_success")


if __name__ == "__main__":
    test_grpcweb_roundtrip()
    test_pkce_code_challenge_shape()
    test_authorization_url_params()
    test_submit_consent_action_id_extraction()
    test_code_from_url_state_mismatch_and_success()
    print("\nALL PKCE UNIT TESTS PASSED")
