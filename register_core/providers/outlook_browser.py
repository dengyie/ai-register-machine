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

# Locale-union selectors. signup.live.com renders its Fluent form in the browser
# locale, and the egress→identity align (outlook_identity.py) now drives that
# locale from the egress IP — a US residential exit yields en-US, so the form's
# aria-labels/visible-text arrive in English. Every locale-sensitive selector
# below carries BOTH the zh-CN and en-US variants and the flow first-matches;
# this keeps creation working on either locale WITHOUT pinning locale to zh-CN
# (which re-introduces the hold that held the LA egress on 2026-08-08). When a
# selector here grows stale against the live DOM, add the new variant rather
# than dropping the zh-CN one — the non-egress path still defaults to zh-CN.
#
# Visible-text variants use Playwright union pseudo-selector `:text-is("a"), …`
# is not directly supported, so the call sites pass a tuple and the matching
# helper takes the first present option. aria-label variants are joined into a
# single CSS :is(...) group per call site.
_CONSENT_CTA_TEXTS = ("同意并继续", "Agree and continue")
_EMAIL_INPUT_ARIA_LABELS = ("新建电子邮件", "New email")
_BIRTH_YEAR_ARIA_LABELS = ("出生年份", "Birth year")
# Month/day option visible-text (role=option in the Fluent listbox). en-US
# months are full names ("January"); day stays the digit.
_MONTH_OPTION_TEXTS = ("1月", "January")
_DAY_OPTION_TEXTS = ("1日", "1")
_ABNORMAL_ACTIVITY_TEXTS = ("一些异常活动", "some unusual activity")
_MAINTENANCE_TEXTS = ("此站点正在维护", "this site is under maintenance")


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
            locale=str(normalized.get("locale") or "zh-CN"),
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


async def _wait_visible_any(
    page: Any,
    aria_labels: tuple[str, ...],
    *,
    inputmode_numeric: bool = False,
    timeout: int = _SLOW_FORM_TIMEOUT_MS,
) -> str:
    """Wait for the first of several locale-variant aria-label controls to mount.

    Returns the aria-label that resolved, so the caller can target the same
    control for fill(). The signup SPA re-mounts per step on the same slow-CDN
    bootstrap, so a visible wait across locale variants (e.g. en-US rendered on
    a US residential egress) replaces a single hard-coded zh-CN aria-label that
    would otherwise time out. ``inputmode_numeric`` narrows to the numeric
    ``input`` variant when set (birth-year uses ``[inputmode="numeric"]``).
    """
    deadline_exc: Exception | None = None
    for label in aria_labels:
        attr = f'[aria-label="{label}"][inputmode="numeric"]' if inputmode_numeric else f'[aria-label="{label}"]'
        loc = page.locator(attr)
        try:
            await loc.wait_for(state="visible", timeout=timeout)
            return label
        except Exception as exc:  # noqa: BLE001 — try next locale variant
            deadline_exc = exc
            continue
    # Re-wait the FIRST variant so the Playwright timeout surfaces to the
    # caller with its standard shape when no locale variant resolves at all.
    if deadline_exc is not None:
        attr = (
            f'[aria-label="{aria_labels[0]}"][inputmode="numeric"]'
            if inputmode_numeric
            else f'[aria-label="{aria_labels[0]}"]'
        )
        await page.locator(attr).wait_for(state="visible", timeout=timeout)
        return aria_labels[0]
    return aria_labels[0]


async def _count_text_any(page: Any, texts: tuple[str, ...], *, exact: bool) -> int:
    """Sum ``get_by_text`` counts across locale variants.

    Used for risk-classification sentinels (abnormal activity / maintenance)
    whose visible text is localized; returns the first non-zero count so a US
    egress's en-US rendered sentinel is detected the same as zh-CN.
    """
    for text in texts:
        try:
            if await page.get_by_text(text, exact=exact).count():
                return 1
        except Exception:  # noqa: BLE001 — try next locale variant
            continue
    return 0


async def _open_dropdown_and_pick(
    page: Any, *, button_selector: str, option_text: str | tuple[str, ...]
) -> None:
    """Fluent UI <Dropdown>: click the trigger button to open a listbox, then
    click the matching role=option by visible text.

    Live WCA (signup.live.com, verified 2026-08-05) renders Birth month/day
    and Country as Fluent UI dropdowns — the trigger is a
    ``button.fui-Dropdown__button`` (NOT a native <select>), and the options
    are ``li[role="option"]`` entries in a listbox that only mounts AFTER the
    trigger is clicked. ``select_option`` cannot target them. The option's
    visible text is the localized label ("1月" / "January", "1日" / "1");
    ``option_text`` accepts a tuple of locale variants and the first one the
    rendered DOM exposes is clicked — locale-union so the egress→identity
    align (en-US for a US exit) does not break the picker. Match exactly so a
    numeric-only option (e.g. the day "1") does not collide with another
    listbox (month names share digits in some locales).
    """
    variants = option_text if isinstance(option_text, tuple) else (option_text,)
    btn = page.locator(button_selector)
    await btn.wait_for(state="visible", timeout=_SLOW_FORM_TIMEOUT_MS)
    await btn.click()
    # First-match across locale variants: a US egress renders en-US labels, so
    # the en-US variant resolves; a zh-CN default locale renders the zh-CN one.
    for text in variants:
        option = page.locator(f'[role="option"]:text-is("{text}")')
        if await option.count():
            await option.first.wait_for(state="visible", timeout=_SLOW_FORM_TIMEOUT_MS)
            await option.first.click()
            return
    # None of the locale variants were present — fall back to the first variant
    # so the caller sees the same Playwright timeout/error shape as before.
    option = page.locator(f'[role="option"]:text-is("{variants[0]}")')
    await option.first.wait_for(state="visible", timeout=_SLOW_FORM_TIMEOUT_MS)
    await option.first.click()


async def _select_birth_field(
    page: Any, *, name: str, css_id: str, value: str, option_text: str | tuple[str, ...]
) -> None:
    """Birth month/day. WCA historically offered a native <select id="BirthMonth">;
    current WCA uses Fluent UI dropdowns with ids ``BirthMonthDropdown`` /
    ``BirthDayDropdown``. Try, in order: native <select> (legacy), Fluent
    dropdown trigger+option (current). The css_id passed in is the LEGACY id;
    the Fluent trigger id is derived by appending ``Dropdown``.
    """
    legacy_selectors = (
        page.locator(f"#{css_id}"),
        page.locator(f'[name="{name}"]'),
    )
    last_error: Exception | None = None
    # 1) Legacy native <select>.
    for locator in legacy_selectors:
        if not await locator.count():
            continue
        try:
            await locator.select_option(value)
            return
        except Exception as exc:  # noqa: BLE001 — legacy UI may be absent
            last_error = exc
    # 2) Current Fluent UI dropdown trigger (id = "<Field>Dropdown").
    fluent_trigger = f"#{css_id}Dropdown"
    if await page.locator(fluent_trigger).count():
        try:
            await _open_dropdown_and_pick(page, button_selector=fluent_trigger, option_text=option_text)
            return
        except Exception as exc:  # noqa: BLE001 — UI variant fallback
            last_error = exc
    # 3) Legacy fallback: click the (now-stale) <select> then role=option.
    variants = option_text if isinstance(option_text, tuple) else (option_text,)
    for locator in legacy_selectors:
        try:
            await locator.click()
            picked = False
            for text in variants:
                opt = page.locator(f'[role="option"]:text-is("{text}")')
                if await opt.count():
                    await opt.first.click()
                    picked = True
                    break
            if not picked:
                # Fall through to the first-variant click below to surface the
                # same Playwright error the single-variant code path used to.
                await page.locator(f'[role="option"]:text-is("{variants[0]}")').click()
            return
        except Exception as exc:  # noqa: BLE001 — try next locator
            last_error = exc
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"unable to set birth field {name}")


async def _fill_birth_year(page: Any, year: str = "1994") -> None:
    """Fill the birth-year input. Live WCA renders it as a numeric
    ``input[aria-label="出生年份"][inputmode="numeric"]`` (NOT ``#BirthYear``);
    a stale ``#BirthYear`` <select> no longer exists. Wait for either form,
    then fill/select across the known variants.
    """
    candidates = (
        # zh-CN rendered form (default locale path).
        page.locator(f'[aria-label="{_BIRTH_YEAR_ARIA_LABELS[0]}"][inputmode="numeric"]'),
        page.locator(f'[aria-label="{_BIRTH_YEAR_ARIA_LABELS[0]}"]'),
        # en-US rendered form (US residential egress → en-US identity align).
        page.locator(f'[aria-label="{_BIRTH_YEAR_ARIA_LABELS[1]}"][inputmode="numeric"]'),
        page.locator(f'[aria-label="{_BIRTH_YEAR_ARIA_LABELS[1]}"]'),
        # Stable id/name invariant across locales (stale on current WCA but harmless).
        page.locator("#BirthYear"),
        page.locator('[name="BirthYear"]'),
    )
    # Wait up to the slow-CDN budget for ANY known birth-year control to mount.
    deadline_hit = False
    for locator in candidates:
        try:
            await locator.wait_for(state="visible", timeout=_SLOW_FORM_TIMEOUT_MS)
            break
        except Exception:  # noqa: BLE001 — try next candidate selector
            continue
    else:
        deadline_hit = True

    last_error: Exception | None = None
    for locator in candidates:
        for action in ("fill", "select_option"):
            try:
                if action == "fill":
                    await locator.fill(year)
                else:
                    await locator.select_option(year)
                return
            except Exception as exc:  # noqa: BLE001 — UI variant fallback
                last_error = exc
    if deadline_hit:
        # No known control mounted at all in the budget — surface that.
        raise RuntimeError("birth year field did not mount within slow-CDN budget")
    if last_error is not None:
        raise last_error
    # Last resort: legacy single-select.
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
        # consent or don't require it. Locale-union: a US egress renders the
        # en-US CTA ("Agree and continue"); try every variant, agree on the
        # first that renders.
        agreed = False
        for consent_text in _CONSENT_CTA_TEXTS:
            try:
                await page.get_by_text(consent_text, exact=True).first.wait_for(
                    state="visible", timeout=_CONSENT_TIMEOUT_MS
                )
                await page.get_by_text(consent_text, exact=True).first.click()
                agreed = True
                break
            except Exception:
                continue
        if not agreed:
            pass  # already past consent or consent not required this session

        # 2) Optional hotmail suffix switch when the option is present
        if await page.locator('[role="option"]:text-is("@hotmail.com")').count():
            await page.locator('[role="option"]:text-is("@hotmail.com")').click()

        # 3) Email local-part — explicitly wait for the input. The signup
        # FluentUI SPA mounts the create-email form only after logincdn.msauth.net
        # finishes bootstrapping (~28s on a slow egress); fill() alone races it.
        # Locale-union aria-label: zh-CN default ("新建电子邮件") OR en-US
        # ("New email") when the egress→identity align drove locale to en-US.
        local_part = email.split("@", 1)[0]
        email_label = await _wait_visible_any(page, _EMAIL_INPUT_ARIA_LABELS)
        await page.locator(f'[aria-label="{email_label}"]').fill(local_part)
        await page.locator('[data-testid="primaryButton"]').click()

        # 4) Password — same SPA re-mount, same slow-CDN lag after the email step.
        await _wait_visible(page, '[type="password"]')
        await page.locator('[type="password"]').fill(password)
        await page.locator('[data-testid="primaryButton"]').click()

        # 5) Birth date — select_option then role=option text fallbacks.
        # Locale-union option_text: zh-CN ("1月"/"1日") OR en-US ("January"/"1").
        await _fill_birth_year(page, "1994")
        await _select_birth_field(
            page, name="BirthMonth", css_id="BirthMonth", value="1", option_text=_MONTH_OPTION_TEXTS
        )
        await _select_birth_field(
            page, name="BirthDay", css_id="BirthDay", value="1", option_text=_DAY_OPTION_TEXTS
        )

        # Submit the birth group — otherwise the SPA never advances to the name
        # screen and #lastNameInput never mounts (probe advanced because it clicks
        # primaryButton after birth; register() was missing this click → 60s stall).
        await page.locator('[data-testid="primaryButton"]').click()

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
        if await _count_text_any(page, _ABNORMAL_ACTIVITY_TEXTS, exact=False):
            return RegistrationPageResult(False, "captcha", "abnormal activity")
        if await _count_text_any(page, _MAINTENANCE_TEXTS, exact=False):
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
