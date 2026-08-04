"""Outlook patchright browser lifecycle and registration form/risk adapter.

Adapted from daimon3332/OutlookRegister (MIT):
https://github.com/daimon3332/OutlookRegister

Browser-heavy paths are only exercised under GROK_REGISTER_OUTLOOK_LIVE=1.
Default unit tests must not launch browsers or create accounts.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from register_core.config.schema import ProviderSpec

# Privacy policy link that remains on the form until registration advances.
_REGISTRATION_COMPLETION_LINK = 'span > [href="https://go.microsoft.com/fwlink/?LinkID=521839"]'
_CREATE_ACCOUNT_URL = "https://outlook.live.com/mail/0/?prompt=create_account"


@dataclass(frozen=True, slots=True)
class OutlookBrowserConfig:
    client_id: str = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
    email_suffix: str = "@outlook.com"
    captcha_strategy: int = 2
    headless: bool = True
    locale: str = "zh-CN"
    timezone_id: str = "UTC"
    latitude: float | None = None
    longitude: float | None = None
    bind_recovery_email: bool = True
    temp_mail: dict[str, Any] | None = None
    outlook_auths_dir: str = "outlook_auths"

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> "OutlookBrowserConfig":
        # Pipeline injects runtime-operational keys into job.extra, which
        # ``get_provider(name, **extra)`` splatters into the provider's options
        # dict (``self.config``). Those are NOT profile-authored config and
        # must never pass the profile secret-guard in ``outlook_options()`` —
        # a runtime-injected ``proxy``/``mail_proxy`` URL is legitimate and is
        # read separately by the adapter from ``extra["proxy"]``; if it reached
        # the guard it would be mis-rejected as a forbidden inline secret.
        runtime_keys = ("proxy", "proxy_list", "mail_proxy", "egress")
        guarded = {k: v for k, v in options.items() if k not in runtime_keys}
        normalized = ProviderSpec(
            name="outlook", options=guarded
        ).outlook_options()
        strategy = normalized["captcha_strategy"]
        return cls(
            client_id=str(normalized.get("client_id") or cls.client_id),
            email_suffix=str(normalized.get("email_suffix") or cls.email_suffix),
            captcha_strategy=strategy,
            headless=bool(normalized.get("headless", True)),
            locale="zh-CN",
            timezone_id=str(normalized.get("timezone_id") or "UTC"),
            latitude=normalized.get("latitude"),
            longitude=normalized.get("longitude"),
            bind_recovery_email=bool(normalized.get("bind_recovery_email", True)),
            temp_mail=dict(normalized.get("temp_mail") or {}),
            outlook_auths_dir=str(normalized.get("outlook_auths_dir") or "outlook_auths"),
        )


@dataclass(slots=True)
class BrowserSession:
    """In-process patchright session owned by OutlookBrowser.open()."""

    browser: Any
    context: Any
    page: Any


@dataclass(frozen=True, slots=True)
class RegistrationPageResult:
    ok: bool
    error_kind: str = ""
    error: str = ""
    captcha_frame_seen: bool = False
    fun_captcha_seen: bool = False


def _proxy_settings(proxy: str | None) -> dict[str, str] | None:
    """Build a patchright proxy object. Never log the proxy value.

    None means intentionally no proxy. Blank/whitespace strings and non-strings
    raise so callers cannot silently drop a required proxy at browser launch.
    """
    if proxy is None:
        return None
    if not isinstance(proxy, str):
        raise TypeError("proxy must be a string server URL or None")
    text = proxy.strip()
    if not text:
        raise ValueError("proxy must be a non-empty server URL")
    return {"server": text}


def _geolocation(config: OutlookBrowserConfig) -> dict[str, float] | None:
    if config.latitude is None or config.longitude is None:
        return None
    return {"latitude": float(config.latitude), "longitude": float(config.longitude)}


class OutlookBrowser:
    """Launch patchright with slidex stealth args; close all resources in finally."""

    def __init__(self, config: OutlookBrowserConfig | None = None) -> None:
        self.config = config or OutlookBrowserConfig()

    @asynccontextmanager
    async def open(self, proxy: str | None = None) -> AsyncIterator[BrowserSession]:
        # Import at use-site so config-only unit tests do not require a live browser stack.
        from patchright.async_api import async_playwright
        from slidex import STEALTH_LAUNCH_ARGS

        proxy_settings = _proxy_settings(proxy)
        geo = _geolocation(self.config)
        async with async_playwright() as playwright:
            browser = context = page = None
            try:
                launch_kwargs: dict[str, Any] = {
                    "headless": bool(self.config.headless),
                    "args": list(STEALTH_LAUNCH_ARGS),
                }
                if proxy_settings is not None:
                    launch_kwargs["proxy"] = proxy_settings
                browser = await playwright.chromium.launch(**launch_kwargs)

                context_kwargs: dict[str, Any] = {
                    "locale": self.config.locale,
                    "timezone_id": self.config.timezone_id,
                }
                if geo is not None:
                    context_kwargs["geolocation"] = geo
                    context_kwargs["permissions"] = ["geolocation"]
                context = await browser.new_context(**context_kwargs)
                # NOTE: intentionally NOT calling context.add_init_script() here.
                # signup.live.com is a React/FluentUI SPA behind a strict CSP
                # (script-src 'self' 'nonce-...'). The slidex STEALTH_INIT_SCRIPT
                # blob injects inline main-world overrides (navigator.webdriver,
                # window.chrome, canvas/WebGL getParameter, etc.) that the CSP
                # rejects; the bootstrap then tags its own nonce-protected
                # <script class="error-handling-tag"> chunks and never mounts the
                # form (CheckAndReportReactBlankPageError swallows the failure),
                # leaving a blank shell with zero inputs. Verified by binary
                # bisect: launch args alone render the consent page fine, the
                # init script alone blanks it. So we keep STEALTH_LAUNCH_ARGS
                # (args are CSP-clean — they change the browser surface, not the
                # page script world) and drop the init script for the Outlook
                # path. Other providers may still use the init script.
                page = await context.new_page()
                yield BrowserSession(browser=browser, context=context, page=page)
            finally:
                # Close in reverse order while playwright is still alive.
                for resource in (page, context, browser):
                    if resource is None:
                        continue
                    try:
                        await resource.close()
                    except Exception:
                        pass


# Worst-case time for the signup FluentUI SPA to bootstrap through the
# proxy egress: logincdn.msauth.net (brs.json → fluent-chunk_vendors →
# fluent-chunk_fluentui → signup-fluent_v2) loads sequentially and can take
# ~28s before the create-email form mounts on a slow node. 60s gives a
# comfortable margin without hanging forever on a genuinely dead page.
_SLOW_FORM_TIMEOUT_MS = 60_000

# Consent ("个人数据导出许可") is a hard pre-condition for the create-email
# form — the SPA does not render any input until consent is granted, and the
# consent page's render time varies widely through the OAuth redirect chain
# (4s on a fast egress, >20s on a slow one). Give consent the same CDN
# bootstrap budget as the form itself so a slow render is still granted.
_CONSENT_TIMEOUT_MS = _SLOW_FORM_TIMEOUT_MS


async def _wait_visible(page: Any, selector: str, *, timeout: int = _SLOW_FORM_TIMEOUT_MS) -> None:
    """Wait for a form control to be visible before interacting.

    The signup SPA re-mounts on every step (email → password → birth → name),
    so each stage hits the same slow-CDN bootstrap lag. Waiting for the
    control's visibility — instead of relying on a fixed fill timeout — keeps
    the flow from racing ahead of the renderer.
    """
    await page.locator(selector).wait_for(state="visible", timeout=timeout)


async def _select_birth_field(page: Any, *, name: str, css_id: str, value: str, option_text: str) -> None:
    """Birth month/day: try select_option, then click role=option text fallback."""
    locators = (
        page.locator(f"#{css_id}"),
        page.locator(f'[name="{name}"]'),
    )
    last_error: Exception | None = None
    for locator in locators:
        try:
            await locator.select_option(value)
            return
        except Exception as exc:  # noqa: BLE001 — UI variant fallback
            last_error = exc
    for locator in locators:
        try:
            await locator.click()
            await page.locator(f'[role="option"]:text-is("{option_text}")').click()
            return
        except Exception as exc:  # noqa: BLE001 — try next locator
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"unable to set birth field {name}")


async def _fill_birth_year(page: Any, year: str = "1994") -> None:
    # Birth controls mount with the SPA after the password step; wait first
    # so the slow-CDN re-mount doesn't race select_option/fill.
    try:
        await page.locator("#BirthYear").wait_for(state="visible", timeout=_SLOW_FORM_TIMEOUT_MS)
    except Exception:  # noqa: BLE001 — fall back to the multi-variant loop below
        pass
    last_error: Exception | None = None
    for locator in (page.locator("#BirthYear"), page.locator('[name="BirthYear"]')):
        for action in ("fill", "select_option"):
            try:
                if action == "fill":
                    await locator.fill(year)
                else:
                    await locator.select_option(year)
                return
            except Exception as exc:  # noqa: BLE001 — UI variant fallback
                last_error = exc
    if last_error is not None:
        raise last_error
    await page.locator("#BirthYear").select_option(year)


class OutlookRegistrationFlow:
    """Five form groups + risk classification before captcha action."""

    async def register(
        self,
        page: Any,
        email: str,
        password: str,
        *,
        captcha_strategy: int,
    ) -> RegistrationPageResult:
        # goto with domcontentloaded (not the default "load", whose networkidle
        # wait would hang on the slow CDN) and a 60s ceiling so the 302 chain
        # (outlook → login → signup) completes even on a slow egress.
        await page.goto(_CREATE_ACCOUNT_URL, wait_until="domcontentloaded", timeout=60_000)

        # 1) Consent — wait (bounded) for the consent button instead of a
        # single poll, so a slow consent render through the OAuth chain is
        # still granted. Timeout is non-fatal: some sessions are already past
        # consent or don't require it.
        try:
            await page.get_by_text("同意并继续", exact=True).first.wait_for(
                state="visible", timeout=_CONSENT_TIMEOUT_MS
            )
        except Exception:
            pass  # already past consent or consent not required this session
        else:
            await page.get_by_text("同意并继续", exact=True).first.click()

        # 2) Optional hotmail suffix switch when the option is present
        if await page.locator('[role="option"]:text-is("@hotmail.com")').count():
            await page.locator('[role="option"]:text-is("@hotmail.com")').click()

        # 3) Email local-part — explicitly wait for the input. The signup
        # FluentUI SPA mounts the create-email form only after logincdn.msauth.net
        # finishes bootstrapping (~28s on a slow egress); fill() alone races it.
        local_part = email.split("@", 1)[0]
        await _wait_visible(page, '[aria-label="新建电子邮件"]')
        await page.locator('[aria-label="新建电子邮件"]').fill(local_part)
        await page.locator('[data-testid="primaryButton"]').click()

        # 4) Password — same SPA re-mount, same slow-CDN lag after the email step.
        await _wait_visible(page, '[type="password"]')
        await page.locator('[type="password"]').fill(password)
        await page.locator('[data-testid="primaryButton"]').click()

        # 5) Birth date — select_option then role=option text fallbacks
        await _fill_birth_year(page, "1994")
        await _select_birth_field(
            page, name="BirthMonth", css_id="BirthMonth", value="1", option_text="1月"
        )
        await _select_birth_field(
            page, name="BirthDay", css_id="BirthDay", value="1", option_text="1日"
        )

        # Name — SPA re-mounts once more before the name screen.
        await _wait_visible(page, "#lastNameInput")
        await page.locator("#lastNameInput").fill("Test")
        await page.locator("#firstNameInput").fill("User")
        await page.locator('[data-testid="primaryButton"]').click()

        # Wait for registration completion marker to detach before mailbox/captcha.
        # Detach is the only positive post-submit signal for form success.
        form_advanced = False
        try:
            await page.locator(_REGISTRATION_COMPLETION_LINK).wait_for(
                state="detached", timeout=22000
            )
            form_advanced = True
        except Exception:
            form_advanced = False

        # Risk classification BEFORE any captcha action. Captcha/risk handoffs
        # still win even when the completion-link wait timed out.
        if await page.locator("iframe#enforcementFrame").count():
            return RegistrationPageResult(
                False,
                "captcha",
                "FunCaptcha enforcement frame",
                fun_captcha_seen=True,
            )
        if await page.get_by_text("一些异常活动", exact=False).count():
            return RegistrationPageResult(False, "captcha", "abnormal activity")
        if await page.get_by_text("此站点正在维护", exact=False).count():
            return RegistrationPageResult(False, "captcha", "maintenance sentinel")

        captcha_seen = await page.locator('iframe[title="验证质询"]').count() > 0
        if captcha_seen and captcha_strategy == 2:
            return RegistrationPageResult(
                False, "captcha", "manual handoff", captcha_frame_seen=True
            )
        if captcha_seen:
            return RegistrationPageResult(
                False, "captcha", "captcha bridge required", captcha_frame_seen=True
            )
        if not form_advanced:
            # Bounded provider/form-timeout failure for later orchestration.
            return RegistrationPageResult(
                False,
                "provider",
                "registration form timeout waiting for completion",
            )
        return RegistrationPageResult(True)
