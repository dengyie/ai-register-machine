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
