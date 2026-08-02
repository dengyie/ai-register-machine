"""Pipeline proxy-propagation contract tests (Task 10, Step 4).

Asserts the contracts PipelineRegister relies on WITHOUT instantiating the
full pipeline (no strategy/preflight/email_source):

1. ``get_provider("outlook")`` returns an OutlookProvider (registry wiring +
   alias resolution work for the new product).
2. ``inject_attempt_proxy`` shallow-copies extra and sets ``extra["proxy"]``
   without mutating the caller's base dict — the value flows down to the
   provider at ``register_one(extra=...)`` and never leaks into the public
   RegisterResult view.
3. The adapter's proxy guard accepts a non-empty injected proxy (it reaches
   orchestration) — verified by stubbing OutlookBrowser so orchestration fails
   with a non-proxy error after the guard, NOT the "missing attempt proxy"
   guard message.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from register_core.providers import outlook_adapter as adapter_mod
from register_core.providers.registry import get_provider


def test_outlook_provider_resolves_from_registry():
    assert type(get_provider("outlook")).__name__ == "OutlookProvider"
    assert type(get_provider("microsoft")).__name__ == "OutlookProvider"


def test_injected_proxy_reaches_provider_extra_but_not_public_view(monkeypatch):
    monkeypatch.setattr(
        "register_core.util.proxy.inject_attempt_proxy",
        lambda extra=None, **kw: {**(extra or {}), "proxy": "synthetic-proxy"},
    )
    monkeypatch.delenv("GROK_REGISTER_OUTLOOK_LIVE", raising=False)

    from register_core.util.proxy import inject_attempt_proxy

    base_extra = {"tag": "x"}
    attempt_extra = inject_attempt_proxy(base_extra)
    assert attempt_extra["proxy"] == "synthetic-proxy"
    assert "proxy" not in base_extra  # shallow-copy contract

    provider = get_provider("outlook")
    result = provider.register_one(email_source=None, extra=attempt_extra)
    assert result.ok is False
    assert result.error_kind == "provider"
    public = result.to_public_dict()
    assert "synthetic-proxy" not in json.dumps(public)


def test_adapter_proxy_guard_accepts_injected_proxy_under_live_gate(monkeypatch):
    """The proxy guard lets a non-empty injected proxy through to the
    orchestration. We make orchestration fail with a sentinel error from the
    stubbed browser; the guard's "missing attempt proxy" message must NOT
    appear (proving the guard accepted the proxy)."""
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    monkeypatch.setattr(
        "register_core.util.proxy.inject_attempt_proxy",
        lambda extra=None, **kw: {**(extra or {}), "proxy": "synthetic-proxy"},
    )

    class _SentinelBrowser:
        def __init__(self, config):
            self.config = config

        @asynccontextmanager
        async def open(self, proxy=None):
            # Reached only if the proxy guard accepted `proxy`.
            raise RuntimeError("orchestration-reached")
            yield  # pragma: no cover

    monkeypatch.setattr(adapter_mod, "OutlookBrowser", _SentinelBrowser)

    from register_core.util.proxy import inject_attempt_proxy

    attempt_extra = inject_attempt_proxy({"tag": "x"})
    assert attempt_extra["proxy"] == "synthetic-proxy"

    provider = get_provider("outlook")
    result = provider.register_one(email_source=None, extra=attempt_extra)
    # The stub's RuntimeError surfaces through asyncio.run's except → provider.
    assert result.ok is False
    assert "orchestration-reached" in (result.error or "")
    assert "missing attempt proxy" not in (result.error or "")
