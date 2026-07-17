#!/usr/bin/env python3
"""error_kind taxonomy constants + normalize (offline)."""

from __future__ import annotations

import unittest

from register_core.contracts import ALLOWED_ERROR_KINDS, normalize_error_kind


class TestErrorKinds(unittest.TestCase):
    def test_allowed_contains_public_taxonomy(self) -> None:
        for k in (
            "mail_miss",
            "registration_disallowed",
            "unsupported_email",
            "already_registered",
            "captcha",
            "session",
            "otp_invalid",
            "oauth_callback",
            "token",
            "proxy",
            "network",
            "provider",
            "verify",
            "fatal",
            "other",
        ):
            self.assertIn(k, ALLOWED_ERROR_KINDS)

    def test_normalize_keeps_known(self) -> None:
        self.assertEqual(normalize_error_kind("registration_disallowed"), "registration_disallowed")
        self.assertEqual(normalize_error_kind("mail_miss"), "mail_miss")
        self.assertEqual(normalize_error_kind("unsupported_email"), "unsupported_email")
        self.assertEqual(normalize_error_kind("already_registered"), "already_registered")
        self.assertEqual(normalize_error_kind("captcha"), "captcha")
        self.assertEqual(normalize_error_kind("session"), "session")
        self.assertEqual(normalize_error_kind("otp_invalid"), "otp_invalid")
        self.assertEqual(normalize_error_kind("oauth_callback"), "oauth_callback")
        self.assertEqual(normalize_error_kind("token"), "token")
        self.assertEqual(normalize_error_kind("proxy"), "proxy")
        self.assertEqual(normalize_error_kind("network"), "network")
        self.assertEqual(normalize_error_kind("fatal"), "fatal")

    def test_normalize_unknown_to_provider(self) -> None:
        self.assertEqual(normalize_error_kind("weird_thing"), "provider")
        self.assertEqual(normalize_error_kind(""), "provider")
        self.assertEqual(normalize_error_kind(None), "provider")  # type: ignore[arg-type]

    def test_normalize_aliases(self) -> None:
        self.assertEqual(normalize_error_kind("disallowed"), "registration_disallowed")
        self.assertEqual(normalize_error_kind("Disallowed"), "registration_disallowed")
        self.assertEqual(normalize_error_kind("registration-disallowed"), "registration_disallowed")
        self.assertEqual(normalize_error_kind("sentinel"), "captcha")
        self.assertEqual(normalize_error_kind("sentinel_fail"), "captcha")
        self.assertEqual(normalize_error_kind("pow"), "captcha")
        self.assertEqual(normalize_error_kind("otp_bad"), "otp_invalid")
        self.assertEqual(normalize_error_kind("bad_otp"), "otp_invalid")
        self.assertEqual(normalize_error_kind("invalid_otp"), "otp_invalid")
        self.assertEqual(normalize_error_kind("oauth"), "oauth_callback")
        self.assertEqual(normalize_error_kind("callback"), "oauth_callback")
        self.assertEqual(normalize_error_kind("missing_oauth_callback"), "oauth_callback")
        self.assertEqual(normalize_error_kind("refresh"), "token")
        self.assertEqual(normalize_error_kind("missing_refresh"), "token")
        self.assertEqual(normalize_error_kind("missing_tokens"), "token")
        self.assertEqual(normalize_error_kind("oauth_token"), "token")

    def test_normalize_prefix_compounds(self) -> None:
        self.assertEqual(normalize_error_kind("oauth_token_http_400"), "token")
        self.assertEqual(normalize_error_kind("missing_token_refresh"), "token")
        self.assertEqual(normalize_error_kind("sentinel_challenge_fail"), "captcha")
        self.assertEqual(normalize_error_kind("captcha:solve_timeout"), "captcha")
        self.assertEqual(normalize_error_kind("session_cookie_missing"), "session")
        self.assertEqual(normalize_error_kind("otp_invalid_http_400"), "otp_invalid")
        self.assertEqual(normalize_error_kind("registration_disallowed_http_400"), "registration_disallowed")
        self.assertEqual(normalize_error_kind("already_registered_409"), "already_registered")
        self.assertEqual(normalize_error_kind("unsupported_email_domain"), "unsupported_email")
        self.assertEqual(normalize_error_kind("oauth_callback_missing"), "oauth_callback")
        self.assertEqual(normalize_error_kind("mail_miss:timeout"), "mail_miss")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
