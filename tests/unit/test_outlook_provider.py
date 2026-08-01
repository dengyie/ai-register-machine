import asyncio

from register_core.contracts import RegisterResult
from register_core.providers.outlook_adapter import OutlookProvider


def test_provider_has_required_protocol_shape():
    provider = OutlookProvider(config={})
    assert provider.name == "outlook"
    assert callable(provider.register_one)


def test_live_browser_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GROK_REGISTER_OUTLOOK_LIVE", raising=False)
    result = OutlookProvider(config={}).register_one(email_source=None, extra={})
    assert isinstance(result, RegisterResult)
    assert result.ok is False
    assert result.error_kind == "provider"
    assert "live gate" in result.error.lower()


def test_live_on_missing_proxy_returns_proxy_error_without_browser(monkeypatch):
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    result = OutlookProvider(config={}).register_one(email_source=None, extra={})
    assert result.ok is False
    assert result.error_kind == "proxy"
    assert "proxy" in result.error.lower()


def test_live_on_blank_or_non_string_proxy_returns_proxy_error(monkeypatch):
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    provider = OutlookProvider(config={})
    for bad in ("", "   ", "\t", None, 123, {"server": "http://x"}):
        result = provider.register_one(email_source=None, extra={"proxy": bad})
        assert result.ok is False, bad
        assert result.error_kind == "proxy", bad
        assert "proxy" in result.error.lower(), bad


def test_live_on_active_event_loop_rejects_without_nested_run(monkeypatch):
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    provider = OutlookProvider(config={})

    async def _inside_loop():
        return provider.register_one(
            email_source=None,
            extra={"proxy": "http://127.0.0.1:7890"},
        )

    result = asyncio.run(_inside_loop())
    assert result.ok is False
    assert result.error_kind == "provider"
    assert "event loop" in result.error.lower()
