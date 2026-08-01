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
        normalized = ProviderSpec(
            name="outlook", options=dict(options)
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
    """Build a patchright proxy object. Never log the proxy value."""
    text = str(proxy or "").strip()
    if not text:
        return None
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
        from slidex import STEALTH_INIT_SCRIPT, STEALTH_LAUNCH_ARGS

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
                await context.add_init_script(STEALTH_INIT_SCRIPT)
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
        await page.goto(_CREATE_ACCOUNT_URL)

        # 1) Consent
        if await page.get_by_text("同意并继续", exact=True).count():
            await page.get_by_text("同意并继续", exact=True).click()

        # 2) Optional hotmail suffix switch when the option is present
        if await page.locator('[role="option"]:text-is("@hotmail.com")').count():
            await page.locator('[role="option"]:text-is("@hotmail.com")').click()

        # 3) Email local-part
        local_part = email.split("@", 1)[0]
        await page.locator('[aria-label="新建电子邮件"]').fill(local_part)
        await page.locator('[data-testid="primaryButton"]').click()

        # 4) Password
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

        # Name
        await page.locator("#lastNameInput").fill("Test")
        await page.locator("#firstNameInput").fill("User")
        await page.locator('[data-testid="primaryButton"]').click()

        # Wait for registration completion marker to detach before mailbox/captcha.
        try:
            await page.locator(_REGISTRATION_COMPLETION_LINK).wait_for(
                state="detached", timeout=22000
            )
        except Exception:
            # Risk checks below still classify the page; do not invent success.
            pass

        # Risk classification BEFORE any captcha action.
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
        return RegistrationPageResult(True)
