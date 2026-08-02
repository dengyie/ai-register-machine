"""Browser-free tests for the Outlook OAuth refresh-token state machine.

Uses asyncio.run (repo convention; pytest-asyncio is not a project dependency).
All emails, passwords, proxies and tokens here are synthetic placeholders.
"""

from __future__ import annotations

import asyncio

import pytest
import requests

from register_core.providers import outlook_oauth
from register_core.providers.outlook_oauth import (
    ACCOUNT_TYPE_TILE,
    CONSENT_ACCEPT,
    KMSI_DENY,
    LOGIN_EMAIL,
    LOGIN_PASSWORD,
    PRIMARY_SUBMIT,
    PROOF_INPUT,
    PROTECT_ACCOUNT,
    PUBLIC_CLIENT_ID,
    OAuthStateMachine,
    OAuthTokenResult,
    OutlookOAuthConfig,
    extract_code,
)

SYNTHETIC_EMAIL = "synthetic@outlook.com"
SYNTHETIC_PASSWORD = "synthetic-password"
SYNTHETIC_PROXY = "http://127.0.0.1:1"
SYNTHETIC_CALLBACK = "https://localhost/?code=synthetic-code"


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------


class _FakeLocator:
    def __init__(self, page: "_FakePage", selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def is_visible(self) -> bool:
        return self._selector in self._page.visible

    async def click(self) -> None:
        self._page.clicks.append(self._selector)

    async def fill(self, value: str) -> None:
        self._page.fills.append((self._selector, value))


class _FakePage:
    def __init__(self, *, url: str = "https://login.live.com/oauth20", visible=()) -> None:
        self.url = url
        self.visible = set(visible)
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []
        self.gotos: list[str] = []

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    async def goto(self, url: str) -> None:
        self.gotos.append(url)

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


class _FakeResponse:
    def __init__(self, payload, *, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


# --------------------------------------------------------------------------
# extract_code
# --------------------------------------------------------------------------


def test_extract_code_accepts_localhost_callback():
    assert extract_code(SYNTHETIC_CALLBACK) == "synthetic-code"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://localhost/",
        "http://localhost/?code=synthetic-code",
        "https://evil.example/?code=synthetic-code",
        "https://localhost.evil.example/?code=synthetic-code",
    ],
)
def test_extract_code_rejects_non_callback_urls(url):
    assert extract_code(url) is None


def test_extract_code_returns_only_the_code_parameter():
    url = (
        "https://localhost/?code=synthetic-code"
        "&session_state=synthetic-session&id_token=synthetic-id-token"
    )
    code = extract_code(url)
    assert code == "synthetic-code"
    assert "synthetic-id-token" not in str(code)


# --------------------------------------------------------------------------
# auth URL
# --------------------------------------------------------------------------


def test_build_auth_url_uses_public_client_and_offline_access():
    url = OutlookOAuthConfig().build_auth_url()
    assert f"client_id={PUBLIC_CLIENT_ID}" in url
    assert "offline_access" in url
    assert "redirect_uri=https%3A%2F%2Flocalhost" in url
    assert "response_type=code" in url


def test_build_auth_url_prompt_none_only_when_sso_preferred():
    assert "prompt=none" in OutlookOAuthConfig().build_auth_url(prefer_sso=True)
    assert "prompt=none" not in OutlookOAuthConfig().build_auth_url(prefer_sso=False)


# --------------------------------------------------------------------------
# redaction
# --------------------------------------------------------------------------


def test_redact_reports_presence_but_never_the_token():
    result = OAuthTokenResult(ok=True, refresh_token="synthetic-refresh", state="refresh_token")
    view = result.redact()
    assert view["has_refresh_token"] is True
    assert "refresh_token" not in view
    assert "synthetic-refresh" not in repr(view)


# --------------------------------------------------------------------------
# state detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("visible", "expected"),
    [
        ((CONSENT_ACCEPT,), "consent"),
        ((ACCOUNT_TYPE_TILE,), "account_type"),
        ((PROOF_INPUT,), "proof_verify"),
        ((PROTECT_ACCOUNT,), "protect_account"),
        ((KMSI_DENY,), "kmsi"),
        ((LOGIN_PASSWORD,), "login_password"),
        ((LOGIN_EMAIL,), "login_email"),
        ((PROOF_INPUT, PROTECT_ACCOUNT), "proof_verify"),
        ((), "unknown"),
    ],
)
def test_state_detection(visible, expected):
    machine = OAuthStateMachine()
    page = _FakePage(visible=visible)
    assert asyncio.run(machine._current_auth_entry_state(page)) == expected


def test_state_detection_without_locator_is_unknown():
    class _Bare:
        url = ""

    machine = OAuthStateMachine()
    assert asyncio.run(machine._current_auth_entry_state(_Bare())) == "unknown"


# --------------------------------------------------------------------------
# state transitions
# --------------------------------------------------------------------------


def _apply(machine, page, state, *, recovery_session=None):
    return asyncio.run(
        machine._apply_state(
            page,
            state,
            full_email=SYNTHETIC_EMAIL,
            password=SYNTHETIC_PASSWORD,
            recovery_session=recovery_session,
        )
    )


def test_login_email_state_fills_and_submits():
    machine = OAuthStateMachine()
    page = _FakePage(visible=(LOGIN_EMAIL,))
    assert _apply(machine, page, "login_email") is True
    assert page.fills == [(LOGIN_EMAIL, SYNTHETIC_EMAIL)]
    assert page.clicks == [PRIMARY_SUBMIT]


def test_login_password_state_fills_and_submits():
    machine = OAuthStateMachine()
    page = _FakePage(visible=(LOGIN_PASSWORD,))
    assert _apply(machine, page, "login_password") is True
    assert page.fills == [(LOGIN_PASSWORD, SYNTHETIC_PASSWORD)]
    assert page.clicks == [PRIMARY_SUBMIT]


def test_kmsi_state_declines_stay_signed_in():
    machine = OAuthStateMachine()
    page = _FakePage(visible=(KMSI_DENY,))
    assert _apply(machine, page, "kmsi") is True
    assert page.clicks == [KMSI_DENY]


def test_proof_state_without_recovery_session_cannot_advance():
    machine = OAuthStateMachine()
    page = _FakePage(visible=(PROOF_INPUT,))
    assert _apply(machine, page, "proof_verify") is False
    assert page.fills == []


def test_proof_state_delegates_to_recovery_session(monkeypatch):
    seen: list[object] = []

    async def fake_verify(page, session, **_kwargs):
        seen.append(session)
        return True

    monkeypatch.setattr(outlook_oauth, "verify_bound_email_on_login", fake_verify)
    machine = OAuthStateMachine()
    page = _FakePage(visible=(PROOF_INPUT,))
    sentinel = object()

    assert _apply(machine, page, "proof_verify", recovery_session=sentinel) is True
    assert seen == [sentinel]
    # The OAuth machine must not type the account password into a proof form.
    assert page.fills == []


# --------------------------------------------------------------------------
# token exchange
# --------------------------------------------------------------------------


def test_exchange_code_applies_proxy_to_http_and_https(monkeypatch):
    captured: dict[str, object] = {}

    def post(url, **kwargs):
        captured.update(kwargs)
        captured["url"] = url
        return _FakeResponse({"refresh_token": "synthetic-refresh"})

    monkeypatch.setattr(outlook_oauth.requests, "post", post)
    machine = OAuthStateMachine()

    machine.exchange_code("synthetic-code", proxy_url=SYNTHETIC_PROXY)

    assert captured["proxies"] == {"http": SYNTHETIC_PROXY, "https": SYNTHETIC_PROXY}
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["client_id"] == PUBLIC_CLIENT_ID


def test_exchange_code_omits_proxies_when_direct(monkeypatch):
    captured: dict[str, object] = {}

    def post(url, **kwargs):
        captured.update(kwargs)
        return _FakeResponse({"refresh_token": "synthetic-refresh"})

    monkeypatch.setattr(outlook_oauth.requests, "post", post)
    OAuthStateMachine().exchange_code("synthetic-code")

    assert captured["proxies"] is None


def test_invalid_grant_is_not_retried(monkeypatch):
    calls: list[str] = []

    def post(url, **kwargs):
        calls.append(kwargs["data"]["code"])
        return _FakeResponse({"error": "invalid_grant"}, status=400)

    monkeypatch.setattr(outlook_oauth.requests, "post", post)
    machine = OAuthStateMachine(proxy_getter=lambda: SYNTHETIC_PROXY)

    with pytest.raises(requests.HTTPError):
        machine.exchange_code("synthetic-code", proxy_url=SYNTHETIC_PROXY)

    # A burnt authorization code must never be replayed.
    assert calls == ["synthetic-code"]


def test_missing_refresh_token_is_not_retried(monkeypatch):
    calls: list[str] = []

    def post(url, **kwargs):
        calls.append(kwargs["data"]["code"])
        return _FakeResponse({"access_token": "synthetic-access"})

    monkeypatch.setattr(outlook_oauth.requests, "post", post)
    machine = OAuthStateMachine(proxy_getter=lambda: SYNTHETIC_PROXY)

    with pytest.raises(ValueError):
        machine.exchange_code("synthetic-code")

    assert calls == ["synthetic-code"]


def test_transport_failure_retries_once_with_rotated_proxy(monkeypatch):
    seen_proxies: list[object] = []

    def post(url, **kwargs):
        seen_proxies.append(kwargs["proxies"])
        if len(seen_proxies) == 1:
            raise requests.ConnectionError("synthetic transport failure")
        return _FakeResponse({"refresh_token": "synthetic-refresh"})

    monkeypatch.setattr(outlook_oauth.requests, "post", post)
    rotated = "http://127.0.0.1:2"
    machine = OAuthStateMachine(proxy_getter=lambda: rotated)

    payload = machine.exchange_code("synthetic-code", proxy_url=SYNTHETIC_PROXY)

    assert payload["refresh_token"] == "synthetic-refresh"
    assert seen_proxies == [
        {"http": SYNTHETIC_PROXY, "https": SYNTHETIC_PROXY},
        {"http": rotated, "https": rotated},
    ]


# --------------------------------------------------------------------------
# _finish error mapping
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "error_kind", "state"),
    [
        (ValueError("missing_refresh_token"), "token", "exchange_missing_refresh"),
        (requests.HTTPError("400"), "token", "exchange_rejected"),
        (requests.ConnectionError("boom"), "proxy", "exchange_transport"),
    ],
)
def test_finish_maps_exchange_failures(monkeypatch, exc, error_kind, state):
    machine = OAuthStateMachine()

    def boom(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(machine, "exchange_code", boom)
    result = machine._finish("synthetic-code", proxy_url="")

    assert result.ok is False
    assert result.error_kind == error_kind
    assert result.state == state
    assert result.refresh_token == ""


# --------------------------------------------------------------------------
# run()
# --------------------------------------------------------------------------


def test_run_returns_refresh_token_without_leaking_it(monkeypatch):
    def post(url, **kwargs):
        return _FakeResponse(
            {"refresh_token": "synthetic-refresh", "access_token": "synthetic-access"}
        )

    monkeypatch.setattr(outlook_oauth.requests, "post", post)
    page = _FakePage(url=SYNTHETIC_CALLBACK)
    machine = OAuthStateMachine()

    result = asyncio.run(
        machine.run(
            page,
            SYNTHETIC_EMAIL,
            SYNTHETIC_PASSWORD,
            proxy=SYNTHETIC_PROXY,
            recovery_session=None,
        )
    )

    assert result.ok is True
    assert result.refresh_token == "synthetic-refresh"
    assert result.state == "refresh_token"
    assert "synthetic-refresh" not in repr(result.redact())
    assert page.gotos and "prompt=none" in page.gotos[0]


def test_run_drives_login_then_callback(monkeypatch):
    def post(url, **kwargs):
        return _FakeResponse({"refresh_token": "synthetic-refresh"})

    monkeypatch.setattr(outlook_oauth.requests, "post", post)

    class _LoginPage(_FakePage):
        async def click_through(self) -> None:
            return None

        def locator(self, selector: str):
            locator = super().locator(selector)
            return locator

    page = _LoginPage(visible=(LOGIN_EMAIL,))

    # After the email form is submitted the identity provider lands on the
    # localhost callback.
    original_fill = _FakeLocator.fill

    async def fill_then_redirect(self, value):
        await original_fill(self, value)
        self._page.visible = set()
        self._page.url = SYNTHETIC_CALLBACK

    monkeypatch.setattr(_FakeLocator, "fill", fill_then_redirect)

    result = asyncio.run(
        machine_run(page, timeout_s=0.01),
    )

    assert result.ok is True
    assert page.fills == [(LOGIN_EMAIL, SYNTHETIC_EMAIL)]


def machine_run(page, *, timeout_s: float = 0.01, recovery_session=None):
    machine = OAuthStateMachine()
    return machine.run(
        page,
        SYNTHETIC_EMAIL,
        SYNTHETIC_PASSWORD,
        proxy="",
        recovery_session=recovery_session,
        timeout_s=timeout_s,
    )


def test_run_without_callback_returns_oauth_callback_kind():
    page = _FakePage(visible=())
    result = asyncio.run(machine_run(page))

    assert result.ok is False
    assert result.error_kind == "oauth_callback"
    assert result.state == "unknown"
    assert result.refresh_token == ""


def test_run_reports_navigation_failure_without_touching_the_network():
    class _DeadPage(_FakePage):
        async def goto(self, url: str) -> None:
            raise RuntimeError("synthetic navigation failure")

    result = asyncio.run(machine_run(_DeadPage()))

    assert result.ok is False
    assert result.error_kind == "oauth_callback"
    assert result.state == "authorize_navigation"


def test_run_stops_when_proof_is_requested_without_recovery_session():
    page = _FakePage(visible=(PROOF_INPUT,))
    result = asyncio.run(machine_run(page))

    assert result.ok is False
    assert result.error_kind == "oauth_callback"
    assert result.state == "proof_verify"
