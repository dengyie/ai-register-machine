import asyncio

import pytest

from register_core.providers.outlook_browser import (
    OutlookBrowserConfig,
    OutlookRegistrationFlow,
    RegistrationPageResult,
    _proxy_settings,
)


def test_outlook_config_defaults_to_manual_captcha_gate():
    """Default is strategy 2 (manual handoff for hold AND FunCaptcha), NOT 0.
    Strategy 0's hold auto-solve needs a HOLD-capable slidex solver; installed
    slidex 0.5.0 VisualChallengeSolver only handles OCR/IMAGE_TEXT and
    SLIDER_CAPTCHA (returns error_code="unsupported_challenge_type" for HOLD),
    so a naive default-0 would dead-bridge live hold captchas instead of
    waiting for the human. Regression guard against re-flipping to 0."""
    config = OutlookBrowserConfig.from_options({})
    assert config.captcha_strategy == 2
    assert config.email_suffix == "@outlook.com"
    assert config.locale == "zh-CN"
    assert config.timezone_id == "UTC"


def test_outlook_config_honors_authored_identity_passthrough():
    """Identity keys (locale/timezone_id/latitude/longitude) are NOT secret keys
    and pass straight through ``outlook_options()`` to the typed config. An
    authored profile value wins — this is the override the egress→identity
    auto-align leaves room for (resolver only fills keys the profile left unset).
    A US/LA residential egress would author a western-aligned identity here
    instead of the zh-CN/UTC default."""
    config = OutlookBrowserConfig.from_options(
        {
            "locale": "en-US",
            "timezone_id": "America/Los_Angeles",
            "latitude": 34.0522,
            "longitude": -118.2437,
        }
    )
    assert config.locale == "en-US"
    assert config.timezone_id == "America/Los_Angeles"
    assert config.latitude == 34.0522
    assert config.longitude == -118.2437


def test_outlook_config_locale_falls_back_to_default_when_unset():
    """An absent locale must keep the zh-CN default (the line-57 hardcode fix uses
    ``normalized.get("locale") or "zh-CN"``). Non-egress paths are unchanged."""
    config = OutlookBrowserConfig.from_options({"timezone_id": "America/Los_Angeles"})
    assert config.locale == "zh-CN"
    assert config.timezone_id == "America/Los_Angeles"


def test_outlook_config_rejects_secret_values_before_normalization():
    with pytest.raises(ValueError, match="secret"):
        OutlookBrowserConfig.from_options({"password": "must-not-be-read"})


def test_outlook_config_rejects_strategy_1_as_not_implemented():
    """Strategy 1 (ManualFallback) is spec-accepted but not wired; rejecting
    it loudly prevents the silent degrade to strategy 0 full-auto HOLD."""
    with pytest.raises(ValueError, match="not implemented"):
        OutlookBrowserConfig.from_options({"captcha_strategy": 1})


def test_outlook_config_accepts_strategy_0_and_2():
    assert OutlookBrowserConfig.from_options({"captcha_strategy": 0}).captcha_strategy == 0
    assert OutlookBrowserConfig.from_options({"captcha_strategy": 2}).captcha_strategy == 2


def test_outlook_config_accepts_pipeline_runtime_proxy_not_guarded():
    """Pipeline injects runtime-operational keys (``proxy``, ``mail_proxy``,
    ``egress``, ``proxy_list``) into ``job.extra`` and ``get_provider(name,
    **extra)`` splatters them into the provider's ``self.config`` options dict.
    Those keys are NOT profile-authored config and must not trip the
    ``outlook_options()`` inline-secret guard — a real runtime proxy URL is
    legitimate and is read separately by the adapter from ``extra["proxy"]``.
    Without this exemption a real live run with ``--proxy`` aborts at the guard
    with "outlook options cannot contain inline secret values: proxy" before
    the proxy handoff boundary is even reached (live regression, 2026-08-03)."""
    for runtime_key in ("proxy", "mail_proxy", "egress", "proxy_list"):
        config = OutlookBrowserConfig.from_options(
            {runtime_key: "http://USER:SECRET@example.invalid:7000",
             "bind_recovery_email": False}
        )
        assert config.email_suffix == "@outlook.com"
        # runtime proxy URL is dropped from the typed config (adapter reads
        # extra["proxy"] separately); it never leaks into OutlookBrowserConfig:
        assert not getattr(config, runtime_key, None)


def test_outlook_config_still_rejects_authored_proxy_in_options():
    """A proxy URL injected anywhere the guard still scans (i.e. nested under a
    profile-authored key like ``temp_mail``) must still be rejected — the
    runtime exemption above only covers the pipeline-injected top-level keys,
    not profile-authored secrets."""
    with pytest.raises(ValueError, match="secret"):
        OutlookBrowserConfig.from_options(
            {"proxy": "http://runtime:legit@example.invalid:7000",  # runtime — OK
             "temp_mail": {"proxy": "http://authored:secret@example.invalid:7000"}}
        )


def test_proxy_settings_accepts_valid_server():
    assert _proxy_settings("http://127.0.0.1:7890") == {"server": "http://127.0.0.1:7890"}
    assert _proxy_settings(None) is None


def test_proxy_settings_rejects_whitespace_instead_of_silent_drop():
    with pytest.raises(ValueError, match="proxy"):
        _proxy_settings("   ")
    with pytest.raises(ValueError, match="proxy"):
        _proxy_settings("")
    with pytest.raises((TypeError, ValueError), match="proxy"):
        _proxy_settings(123)  # type: ignore[arg-type]


class _FakeCount:
    def __init__(self, n: int) -> None:
        self._n = n

    async def count(self) -> int:
        return self._n


class _FakeLocator:
    def __init__(self, *, count: int = 0, wait_error: Exception | None = None) -> None:
        self._count = count
        self._wait_error = wait_error
        self.fill_calls: list[str] = []
        self.click_calls = 0
        self.select_calls: list[str] = []

    def count(self):  # sync-or-await dual use via awaitable wrapper below
        return _AwaitableInt(self._count)

    async def wait_for(self, **kwargs):
        if self._wait_error is not None:
            raise self._wait_error
        return None

    async def fill(self, value: str) -> None:
        self.fill_calls.append(value)

    async def click(self) -> None:
        self.click_calls += 1

    async def select_option(self, value: str) -> None:
        self.select_calls.append(value)


class _AwaitableInt:
    def __init__(self, value: int) -> None:
        self._value = value

    def __await__(self):
        async def _coro():
            return self._value

        return _coro().__await__()

    def __bool__(self) -> bool:
        return bool(self._value)

    def __gt__(self, other: int) -> bool:
        return self._value > other


class _FakeText:
    def __init__(self, present: bool = False, *, owner=None, text: str = "") -> None:
        self._present = present
        self._owner = owner
        self._text = text

    def count(self):
        return _AwaitableInt(1 if self._present else 0)

    @property
    def first(self) -> "_FakeText":
        # Playwright's Locator.first — used for the consent wait.
        return self

    async def wait_for(self, **kwargs):
        if not self._present:
            raise TimeoutError(f"{self._text} not visible")
        return None

    async def click(self) -> None:
        if self._owner is not None:
            self._owner.consent_text = self._text


class _FakePage:
    """Browser-free page stub for form/risk classification unit tests."""

    def __init__(
        self,
        *,
        detach_timeout: bool = False,
        enforcement: bool = False,
        abnormal: bool = False,
        maintenance: bool = False,
        captcha_frame: bool = False,
        consent: bool = False,
        locale: str = "zh-CN",
    ) -> None:
        self.detach_timeout = detach_timeout
        self.enforcement = enforcement
        self.abnormal = abnormal
        self.maintenance = maintenance
        self.captcha_frame = captcha_frame
        self.consent = consent
        # ``locale`` drives which rendered variant the egress→identity align
        # would have selected; the stub surfaces only the chosen variant so a
        # test exercising the en-US form path can assert the previous zh-CN-only
        # hard-codes (now locale-union selectors) keep working in English.
        self.locale = locale
        self.consent_text: str = ""
        self.goto_calls: list[str] = []

    async def goto(self, url: str, **_kwargs) -> None:
        self.goto_calls.append(url)

    def locator(self, selector: str) -> _FakeLocator:
        if 'href="https://go.microsoft.com/fwlink/?LinkID=521839"' in selector:
            err = TimeoutError("detach timeout") if self.detach_timeout else None
            return _FakeLocator(wait_error=err)
        if selector == "iframe#enforcementFrame":
            return _FakeLocator(count=1 if self.enforcement else 0)
        if 'title="验证质询"' in selector:
            return _FakeLocator(count=1 if self.captcha_frame else 0)
        # Form controls — always present and no-op.
        return _FakeLocator(count=0)

    def get_by_text(self, text: str, exact: bool = False):
        # Only the variant matching the rendered locale is present; the other
        # locale variant resolves to not-present so the locale-union loops are
        # actually exercising the alternate path rather than short-circuiting
        # on the first (zh-CN) variant.
        zh_variant_for = {
            "同意并继续": self.consent,
            "一些异常活动": self.abnormal,
            "此站点正在维护": self.maintenance,
        }
        en_variant_for = {
            "Agree and continue": self.consent,
            "some unusual activity": self.abnormal,
            "this site is under maintenance": self.maintenance,
        }
        if self.locale == "en-US":
            present = en_variant_for.get(text, False)
            return _FakeText(present, owner=self, text=text)
        present = zh_variant_for.get(text, False)
        return _FakeText(present, owner=self, text=text)


def test_registration_flow_detach_timeout_without_risk_is_not_success():
    page = _FakePage(detach_timeout=True)
    result = asyncio.run(
        OutlookRegistrationFlow().register(
            page,
            "user@outlook.com",
            "Pw-not-logged",
            captcha_strategy=2,
        )
    )
    assert isinstance(result, RegistrationPageResult)
    assert result.ok is False
    assert result.error_kind == "provider"
    assert "form" in result.error.lower() or "timeout" in result.error.lower()
    assert result.captcha_frame_seen is False
    assert result.fun_captcha_seen is False


def test_registration_flow_detach_timeout_keeps_captcha_handoff():
    page = _FakePage(detach_timeout=True, captcha_frame=True)
    result = asyncio.run(
        OutlookRegistrationFlow().register(
            page,
            "user@outlook.com",
            "Pw-not-logged",
            captcha_strategy=2,
        )
    )
    assert result.ok is False
    assert result.error_kind == "captcha"
    assert result.captcha_frame_seen is True
    assert "manual handoff" in result.error.lower()


def test_registration_flow_enforcement_frame_before_success():
    page = _FakePage(enforcement=True)
    result = asyncio.run(
        OutlookRegistrationFlow().register(
            page,
            "user@outlook.com",
            "Pw-not-logged",
            captcha_strategy=2,
        )
    )
    assert result.ok is False
    assert result.error_kind == "captcha"
    assert result.fun_captcha_seen is True


def test_registration_flow_clicks_consent_when_present():
    """The consent button (同意并继续) is a hard gate: when present, register()
    must wait for it and click it — a single poll would miss a slow render."""
    page = _FakePage(consent=True)
    asyncio.run(
        OutlookRegistrationFlow().register(
            page,
            "user@outlook.com",
            "Pw-not-logged",
            captcha_strategy=2,
        )
    )
    assert page.consent_text == "同意并继续", "consent should have been clicked"


def test_registration_flow_consent_absent_is_non_fatal():
    """When no consent button appears (already past consent or not required),
    register() must not raise — it proceeds to the email form wait. The
    consent timeout is non-fatal; the flow continues normally."""
    page = _FakePage(consent=False)
    # Should not raise despite consent timeout
    result = asyncio.run(
        OutlookRegistrationFlow().register(
            page,
            "user@outlook.com",
            "Pw-not-logged",
            captcha_strategy=2,
        )
    )
    # Consent timeout is silently swallowed; form proceeds normally.
    # Since no detach timeout or captcha, result is ok=True.
    assert result.ok is True
    assert page.consent_text == ""  # consent was never clicked (not required)


def test_registration_flow_clicks_en_us_consent_when_present():
    """egress→identity align drives locale to en-US for a US residential exit,
    so the form (and its consent CTA) renders in English. The locale-union
    consent loop must click the en-US "Agree and continue" the same it would
    the zh-CN one — regression guard so a US egress does not silently stall at
    the consent step the way the 2026-08-09 live run stalled at the email step.
    """
    page = _FakePage(consent=True, locale="en-US")
    asyncio.run(
        OutlookRegistrationFlow().register(
            page,
            "user@outlook.com",
            "Pw-not-logged",
            captcha_strategy=2,
        )
    )
    assert page.consent_text == "Agree and continue", "en-US consent should have been clicked"


def test_registration_flow_detects_en_us_abnormal_activity():
    """Risk classification must detect the en-US rendered abnormal-activity
    sentinel, not only the zh-CN one. ``_count_text_any`` walks locale variants."""
    page = _FakePage(abnormal=True, locale="en-US")
    result = asyncio.run(
        OutlookRegistrationFlow().register(
            page,
            "user@outlook.com",
            "Pw-not-logged",
            captcha_strategy=2,
        )
    )
    assert result.ok is False
    assert result.error_kind == "captcha"
    assert "abnormal activity" in result.error.lower()


def test_registration_flow_detects_en_us_maintenance():
    """Same locale-union guarantee for the maintenance sentinel."""
    page = _FakePage(maintenance=True, locale="en-US")
    result = asyncio.run(
        OutlookRegistrationFlow().register(
            page,
            "user@outlook.com",
            "Pw-not-logged",
            captcha_strategy=2,
        )
    )
    assert result.ok is False
    assert result.error_kind == "captcha"
    assert "maintenance" in result.error.lower()
