import asyncio

import pytest

from register_core.providers.outlook_browser import (
    OutlookBrowserConfig,
    OutlookRegistrationFlow,
    RegistrationPageResult,
    _proxy_settings,
)


def test_outlook_config_defaults_to_manual_captcha_gate():
    config = OutlookBrowserConfig.from_options({})
    assert config.captcha_strategy == 2
    assert config.email_suffix == "@outlook.com"
    assert config.locale == "zh-CN"
    assert config.timezone_id == "UTC"


def test_outlook_config_rejects_secret_values_before_normalization():
    with pytest.raises(ValueError, match="secret"):
        OutlookBrowserConfig.from_options({"password": "must-not-be-read"})


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
    def __init__(self, present: bool = False) -> None:
        self._present = present

    def count(self):
        return _AwaitableInt(1 if self._present else 0)

    async def click(self) -> None:
        return None


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
    ) -> None:
        self.detach_timeout = detach_timeout
        self.enforcement = enforcement
        self.abnormal = abnormal
        self.maintenance = maintenance
        self.captcha_frame = captcha_frame
        self.goto_calls: list[str] = []

    async def goto(self, url: str) -> None:
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
        if text == "同意并继续":
            return _FakeText(False)
        if text == "一些异常活动":
            return _FakeText(self.abnormal)
        if text == "此站点正在维护":
            return _FakeText(self.maintenance)
        return _FakeText(False)


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
