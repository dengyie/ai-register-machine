"""Outlook OAuth2 authorization-code flow and refresh-token exchange.

Adapted from daimon3332/OutlookRegister (MIT):
https://github.com/daimon3332/OutlookRegister

The public Microsoft client id below is the well-known desktop client used by
mail clients; no client secret exists or is required. The state machine drives
the consent hop in the *same* browser context that registered the account, so
the freshly created session is reused instead of logging in from scratch.

Security: the authorization code, the callback URL, ``state``, cookies, and the
refresh token never reach logs, exception messages, or ``metadata``. The refresh
token is returned in memory only; persisting it is the adapter's private 0600
sink responsibility (Task 9).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from register_core.providers.outlook_recovery import verify_bound_email_on_login

PUBLIC_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
DEFAULT_REDIRECT_URI = "https://localhost"
DEFAULT_SCOPE = "https://graph.microsoft.com/.default offline_access"
AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

# Microsoft identity selectors (stable across zh-CN / en-US).
ACCOUNT_TYPE_TILE = "#msaTile"
LOGIN_EMAIL = "#i0116"
LOGIN_PASSWORD = "#i0118"
PRIMARY_SUBMIT = "#idSIButton9"
KMSI_DENY = "#declineButton, [data-testid=secondaryButton], input[value=否]"
CONSENT_ACCEPT = '[data-testid="appConsentPrimaryButton"]'
PROTECT_ACCOUNT = "#EmailAddress"
PROOF_INPUT = "#iOttText, #codeEntry-0"

# Public, bounded state names — safe to place in metadata.
STATES = (
    "consent",
    "account_type",
    "protect_account",
    "proof_verify",
    "kmsi",
    "login_email",
    "login_password",
    "unknown",
)


def extract_code(url: str) -> str | None:
    """Return only the ``code`` parameter of a localhost callback URL."""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.netloc != "localhost":
        return None
    return parse_qs(parsed.query).get("code", [None])[0]


@dataclass(frozen=True, slots=True)
class OutlookOAuthConfig:
    client_id: str = PUBLIC_CLIENT_ID
    redirect_uri: str = DEFAULT_REDIRECT_URI
    scope: str = DEFAULT_SCOPE
    authorize_url: str = AUTHORIZE_URL
    token_url: str = TOKEN_URL

    def build_auth_url(self, prefer_sso: bool = True) -> str:
        """Authorization-code URL; ``prefer_sso`` attempts a silent hop first."""
        query = {
            "client_id": self.client_id,
            "response_type": "code",
            "response_mode": "query",
            "redirect_uri": self.redirect_uri,
            "scope": self.scope,
        }
        if prefer_sso:
            query["prompt"] = "none"
        return f"{self.authorize_url}?{urlencode(query)}"


@dataclass(slots=True)
class OAuthTokenResult:
    ok: bool
    refresh_token: str = ""
    error_kind: str = ""
    state: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def redact(self) -> dict[str, Any]:
        """Public view: token presence only, never the token itself."""
        return {
            "ok": self.ok,
            "has_refresh_token": bool(self.refresh_token),
            "error_kind": self.error_kind,
            "state": self.state,
            "metadata": dict(self.metadata),
        }


class OAuthStateMachine:
    """Drives the consent hop and exchanges the code for a refresh token."""

    def __init__(
        self,
        *,
        config: OutlookOAuthConfig | None = None,
        proxy_getter: Callable[[], str] | None = None,
        max_transitions: int = 24,
    ) -> None:
        self.config = config or OutlookOAuthConfig()
        self.proxy_getter = proxy_getter
        self.max_transitions = int(max_transitions)

    # ---------------- page helpers ----------------

    async def _visible(self, page: Any, selector: str) -> bool:
        locator = getattr(page, "locator", None)
        if locator is None:
            return False
        try:
            return bool(await locator(selector).first.is_visible())
        except Exception:
            return False

    async def _current_auth_entry_state(self, page: Any) -> str:
        """Classify the current identity page into one bounded state name."""
        for selector, state in (
            (CONSENT_ACCEPT, "consent"),
            (ACCOUNT_TYPE_TILE, "account_type"),
            (PROOF_INPUT, "proof_verify"),
            (PROTECT_ACCOUNT, "protect_account"),
            (KMSI_DENY, "kmsi"),
            (LOGIN_PASSWORD, "login_password"),
            (LOGIN_EMAIL, "login_email"),
        ):
            if await self._visible(page, selector):
                return state
        return "unknown"

    async def _wait_for_code_capture(self, page: Any, timeout_s: float) -> str | None:
        """Poll the page URL for the localhost callback code; logs nothing."""
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            code = extract_code(str(getattr(page, "url", "") or ""))
            if code:
                return code
            if time.monotonic() >= deadline:
                return None
            waiter = getattr(page, "wait_for_timeout", None)
            if waiter is None:
                return None
            try:
                await waiter(250)
            except Exception:
                return None

    # ---------------- token exchange ----------------

    def _exchange_code_once(
        self,
        code: str,
        redirect_uri: str,
        proxy_url: str,
    ) -> dict[str, Any]:
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        response = requests.post(
            self.config.token_url,
            data={
                "client_id": self.config.client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "scope": self.config.scope,
            },
            proxies=proxies,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not payload.get("refresh_token"):
            raise ValueError("missing_refresh_token")
        return payload

    def exchange_code(self, code: str, *, proxy_url: str = "") -> dict[str, Any]:
        """Exchange the code once, retrying only on transport/proxy failures."""
        try:
            return self._exchange_code_once(code, self.config.redirect_uri, proxy_url)
        except (requests.HTTPError, ValueError):
            # Invalid authorization or a response without refresh_token is
            # terminal — a retry would only replay a burnt code.
            raise
        except requests.RequestException:
            retry_proxy = proxy_url
            if self.proxy_getter is not None:
                try:
                    retry_proxy = str(self.proxy_getter() or proxy_url)
                except Exception:
                    retry_proxy = proxy_url
            return self._exchange_code_once(code, self.config.redirect_uri, retry_proxy)

    # ---------------- state transitions ----------------

    async def _click(self, page: Any, selector: str) -> None:
        await page.locator(selector).first.click()

    async def _apply_state(
        self,
        page: Any,
        state: str,
        *,
        full_email: str,
        password: str,
        recovery_session: Any,
    ) -> bool:
        """Advance one state; False means the flow cannot continue."""
        if state == "consent":
            await self._click(page, CONSENT_ACCEPT)
            return True
        if state == "account_type":
            await self._click(page, ACCOUNT_TYPE_TILE)
            return True
        if state == "login_email":
            await page.locator(LOGIN_EMAIL).fill(full_email)
            await self._click(page, PRIMARY_SUBMIT)
            return True
        if state == "login_password":
            await page.locator(LOGIN_PASSWORD).fill(password)
            await self._click(page, PRIMARY_SUBMIT)
            return True
        if state == "kmsi":
            await self._click(page, KMSI_DENY)
            return True
        if state in ("protect_account", "proof_verify"):
            if recovery_session is None:
                return False
            return bool(await verify_bound_email_on_login(page, recovery_session))
        return False

    # ---------------- orchestration ----------------

    async def run(
        self,
        page: Any,
        full_email: str,
        password: str,
        *,
        proxy: str = "",
        recovery_session: Any = None,
        timeout_s: float = 20.0,
    ) -> OAuthTokenResult:
        state = ""
        try:
            await page.goto(self.config.build_auth_url(prefer_sso=True))
        except Exception:
            return OAuthTokenResult(
                ok=False,
                error_kind="oauth_callback",
                state="authorize_navigation",
            )

        for _ in range(self.max_transitions):
            code = await self._wait_for_code_capture(page, timeout_s=timeout_s)
            if code:
                return self._finish(code, proxy_url=proxy)
            state = await self._current_auth_entry_state(page)
            if state == "unknown":
                break
            try:
                advanced = await self._apply_state(
                    page,
                    state,
                    full_email=full_email,
                    password=password,
                    recovery_session=recovery_session,
                )
            except Exception:
                return OAuthTokenResult(
                    ok=False, error_kind="oauth_callback", state=state
                )
            if not advanced:
                break

        return OAuthTokenResult(
            ok=False,
            error_kind="oauth_callback",
            state=state or "unknown",
            metadata={"reason": "callback_not_reached"},
        )

    def _finish(self, code: str, *, proxy_url: str) -> OAuthTokenResult:
        try:
            payload = self.exchange_code(code, proxy_url=proxy_url)
        except ValueError:
            return OAuthTokenResult(
                ok=False, error_kind="token", state="exchange_missing_refresh"
            )
        except requests.HTTPError:
            return OAuthTokenResult(
                ok=False, error_kind="token", state="exchange_rejected"
            )
        except requests.RequestException:
            return OAuthTokenResult(
                ok=False, error_kind="proxy", state="exchange_transport"
            )
        return OAuthTokenResult(
            ok=True,
            refresh_token=str(payload.get("refresh_token") or ""),
            state="refresh_token",
            metadata={"scope_requested": self.config.scope},
        )
