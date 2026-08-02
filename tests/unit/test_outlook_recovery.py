"""Unit tests for Outlook recovery binding and login proof reuse (Task 7).

Browser-free: pages are fakes, no patchright/Playwright launch, no real account.
Uses asyncio.run (repo convention; pytest-asyncio is not a project dependency).
"""

from __future__ import annotations

import asyncio

import pytest

from register_core.contracts import Mailbox, OtpCode
from register_core.errors import MailMissError, ProviderError
from register_core.providers.outlook_recovery import (
    RecoverySession,
    bind_recovery_email,
    verify_bound_email_on_login,
)


class _FakeLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    async def is_visible(self):
        return self._selector in self._page.visible

    async def fill(self, value):
        self._page.filled.append((self._selector, value))

    async def click(self):
        self._page.clicked.append(self._selector)


class _FakePage:
    """Minimal page exposing only locator(), like the real Playwright surface."""

    def __init__(self, visible=()):
        self.visible = set(visible)
        self.filled: list[tuple[str, str]] = []
        self.clicked: list[str] = []

    def locator(self, selector):
        # Comma-joined selectors are visible when any single member is.
        if "," in selector:
            for part in (p.strip() for p in selector.split(",")):
                if part in self.visible:
                    return _FakeLocator(self, part)
        return _FakeLocator(self, selector)


class _Source:
    name = "cf_temp"

    def __init__(self, code="123456"):
        self.calls: list[str] = []
        self._code = code

    def allocate(self):
        self.calls.append("allocate")
        return Mailbox(address="throwaway@invalid", token="synthetic-jwt", provider="cf_temp")

    def poll_otp(self, mailbox, **kwargs):
        self.calls.append("poll")
        return OtpCode(code=self._code, source="cf_temp")

    def release(self, mailbox, *, success):
        self.calls.append("release")


def test_bound_session_is_reused_without_allocating_new_address():
    """A bare page requests no proof: return False and never allocate."""
    source = _Source()
    source.allocate = lambda: (_ for _ in ()).throw(AssertionError("must reuse session"))
    session = RecoverySession(mailbox=object(), source=source, bound=True)
    page = type("Page", (), {})()

    assert asyncio.run(verify_bound_email_on_login(page, session)) is False
    assert source.calls == []


def test_proof_challenge_reuses_bound_mailbox():
    source = _Source()
    mailbox = Mailbox(address="throwaway@invalid", token="synthetic-jwt", provider="cf_temp")
    session = RecoverySession(mailbox=mailbox, source=source, bound=True)
    page = _FakePage(visible={"#iOttText", "#idSIButton9"})

    assert asyncio.run(verify_bound_email_on_login(page, session)) is True
    assert source.calls == ["poll"]  # polled, never allocated
    assert ("#iOttText", "123456") in page.filled
    assert session.meta["used_codes"] == {"123456"}


def test_proof_challenge_fills_six_split_code_cells():
    source = _Source()
    session = RecoverySession(
        mailbox=Mailbox(address="throwaway@invalid", provider="cf_temp"),
        source=source,
        bound=True,
    )
    page = _FakePage(visible={"#codeEntry-0", "#idSIButton9"})

    assert asyncio.run(verify_bound_email_on_login(page, session)) is True
    assert page.filled == [(f"#codeEntry-{i}", d) for i, d in enumerate("123456")]


def test_unbound_session_refuses_proof_instead_of_allocating():
    source = _Source()
    session = RecoverySession(mailbox=object(), source=source, bound=False)
    page = _FakePage(visible={"#iOttText"})

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(verify_bound_email_on_login(page, session))
    assert "recovery_not_bound" in str(excinfo.value)
    assert source.calls == []


def test_bind_returns_none_when_no_protect_page():
    source = _Source()
    page = _FakePage(visible=set())

    assert asyncio.run(bind_recovery_email(page, source)) is None
    assert source.calls == []


def test_bind_allocates_once_and_marks_session_bound():
    source = _Source()
    page = _FakePage(visible={"#EmailAddress", "#iOttText", "#idSIButton9"})

    session = asyncio.run(bind_recovery_email(page, source))
    assert session is not None
    assert session.bound is True
    assert source.calls == ["allocate", "poll"]
    assert ("#EmailAddress", "throwaway@invalid") in page.filled
    assert session.redact() == {
        "bound": True,
        "address": "throwaway@invalid",
        "source": "cf_temp",
    }


def test_bind_dismisses_passkey_interstitial_first():
    source = _Source()
    page = _FakePage(visible={"#idBtn_Back", "#EmailAddress", "#iOttText", "#idSIButton9"})

    asyncio.run(bind_recovery_email(page, source))
    assert page.clicked[0] == "#idBtn_Back"


def test_mail_miss_maps_to_provider_error_without_message_text():
    source = _Source()

    def raise_miss(mailbox, **kwargs):
        source.calls.append("poll")
        raise MailMissError("cf_temp no OTP for throwaway@invalid before deadline")

    source.poll_otp = raise_miss
    session = RecoverySession(
        mailbox=Mailbox(address="throwaway@invalid", provider="cf_temp"),
        source=source,
        bound=True,
    )
    page = _FakePage(visible={"#iOttText"})

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(verify_bound_email_on_login(page, session))
    assert str(excinfo.value) == "mail_miss"


def test_non_six_digit_code_maps_to_otp_invalid():
    source = _Source(code="12ab")
    session = RecoverySession(
        mailbox=Mailbox(address="throwaway@invalid", provider="cf_temp"),
        source=source,
        bound=True,
    )
    page = _FakePage(visible={"#iOttText"})

    with pytest.raises(ProviderError) as excinfo:
        asyncio.run(verify_bound_email_on_login(page, session))
    assert "otp_invalid" in str(excinfo.value)
