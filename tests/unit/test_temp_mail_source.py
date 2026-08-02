"""Unit tests for the CF Temp Mail source (Task 7).

Browser-free and network-free: every HTTP boundary is monkeypatched, and all
addresses/codes/JWTs are synthetic.
"""

from __future__ import annotations

import json

import pytest

from register_core.contracts import Mailbox
from register_core.email.registry import get_email_source, list_email_sources
from register_core.email.sources.temp_mail import TempMailClient, TempMailSource
from register_core.errors import MailMissError, ProviderError


@pytest.mark.parametrize(
    "body",
    [
        {"subject": "Security code", "text": "Use code: 123456"},
        {"subject": "验证码", "text": "验证码 654321"},
    ],
)
def test_extract_code_from_synthetic_mail(body):
    assert TempMailClient.extract_code_from_text(json.dumps(body)) in {"123456", "654321"}


def test_extract_code_returns_none_without_six_digits():
    assert TempMailClient.extract_code_from_text("no code here, only 12345") is None
    assert TempMailClient.extract_code_from_text("") is None


def _source() -> TempMailSource:
    return TempMailSource(
        base_url="https://mail.invalid",
        admin_password_env="TEST_TEMP_MAIL_ADMIN",
        domain="invalid",
    )


def test_registry_source_has_no_password_in_redacted_mailbox(monkeypatch):
    source = _source()
    monkeypatch.setenv("TEST_TEMP_MAIL_ADMIN", "synthetic-admin")
    mailbox = source._mailbox_from_payload({"address": "a@invalid", "jwt": "synthetic-jwt"})
    assert mailbox.redact() == {
        "address": "a@invalid",
        "provider": "cf_temp",
        "has_token": True,
        "meta_keys": [],
    }


def test_allocate_without_admin_env_raises_before_any_http(monkeypatch):
    source = _source()
    monkeypatch.delenv("TEST_TEMP_MAIL_ADMIN", raising=False)

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("no HTTP call may happen without the admin env")

    monkeypatch.setattr("register_core.email.sources.temp_mail.httpx.post", explode)
    with pytest.raises(ProviderError) as excinfo:
        source.allocate()
    assert "temp_mail_admin_env_missing" in str(excinfo.value)


def test_poll_otp_returns_fresh_code_and_skips_used(monkeypatch):
    source = _source()
    mails = [
        {"subject": "Security code", "text": "code: 111111", "timestamp": 100},
        {"subject": "Security code", "text": "code: 222222", "timestamp": 200},
    ]
    monkeypatch.setattr(TempMailClient, "list_mails", lambda self, mailbox: mails)
    mailbox = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")

    otp = source.poll_otp(mailbox, timeout_s=1, poll_interval_s=0.1, used_codes={"111111"})
    assert otp.code == "222222"
    assert otp.source == "cf_temp"
    assert source.last_wait_diagnostics is not None
    assert source.last_wait_diagnostics.provider == "cf_temp"


def test_poll_otp_raises_mail_miss_with_diagnostics(monkeypatch):
    source = _source()
    monkeypatch.setattr(TempMailClient, "list_mails", lambda self, mailbox: [])
    monkeypatch.setattr("register_core.email.sources.temp_mail.time.sleep", lambda _s: None)
    mailbox = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")

    with pytest.raises(MailMissError) as excinfo:
        source.poll_otp(mailbox, timeout_s=0.2, poll_interval_s=0.1)
    diagnostics = excinfo.value.diagnostics
    assert diagnostics is not None
    assert diagnostics.failure_class == "no_mail"
    # No mailbox JWT may leak into the failure message.
    assert "synthetic-jwt" not in str(excinfo.value)


def test_poll_otp_drops_mail_older_than_watermark(monkeypatch):
    source = _source()
    monkeypatch.setattr(
        TempMailClient,
        "list_mails",
        lambda self, mailbox: [{"subject": "old", "text": "code: 333333", "timestamp": 100}],
    )
    monkeypatch.setattr("register_core.email.sources.temp_mail.time.sleep", lambda _s: None)
    mailbox = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")

    with pytest.raises(MailMissError):
        source.poll_otp(mailbox, timeout_s=0.2, poll_interval_s=0.1, newer_than_epoch=500)


def test_release_is_noop():
    source = _source()
    mailbox = Mailbox(address="a@invalid", token="synthetic-jwt", provider="cf_temp")
    assert source.release(mailbox, success=True) is None


def test_registry_exposes_both_aliases_but_not_in_auto():
    names = list_email_sources()
    assert "temp_mail" in names
    assert "cf_temp" in names
    source = get_email_source(
        "cf_temp",
        base_url="https://mail.invalid",
        admin_password_env="TEST_TEMP_MAIL_ADMIN",
        domain="invalid",
    )
    assert isinstance(source, TempMailSource)
    # "auto" must keep preferring the pooled sources; cf_temp is opt-in only.
    assert not isinstance(get_email_source("auto"), TempMailSource)
