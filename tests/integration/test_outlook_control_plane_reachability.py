"""Control-plane to pipeline reachability for Outlook (Task #206).

Pins the full in-process chain WITHOUT touching the network or a real
browser/mailbox:

    get_provider("outlook")  →  real OutlookProvider
        →  Pipeline.run(count=N, fail_fast=...)  (proxy preflight mocked)
            →  OutlookProvider.register_one()  short-circuits at the live gate
                →  RegisterResult(ok=False, error_kind="provider")
                    →  pipeline classifies as terminal provider, stats.fail++,
                       and (under fail_fast=True) stops after the first attempt.

This closes the gap left by ``test_outlook_pipeline_gated`` (Task 11), which
fakes the provider entirely with ``FakeOutlookProvider``: it never reaches
the REAL registry-resolved OutlookProvider nor the REAL live gate, so a
regression that breaks the gate short-circuit (e.g. moving network work above
the gate) would slip past it. Here the provider is real; only the shared
node/egress proxy preflight is mocked — it would otherwise hit the network
per dead node (~12s libcurl timeouts), which is environment-dependent and
not part of the reachability contract.

Live-browser behaviour is NOT exercised; this is mock-environment only and is
explicitly skipped when ``GROK_REGISTER_OUTLOOK_LIVE=1``.
"""

from __future__ import annotations

import os

import pytest

from register_core.pipeline import Pipeline
from register_core.providers.outlook_adapter import OutlookProvider
from register_core.providers.registry import get_provider

pytestmark = pytest.mark.skipif(
    os.environ.get("GROK_REGISTER_OUTLOOK_LIVE") == "1",
    reason="acceptance test is mock-only; live browser tests are separately gated",
)


def _mock_proxy_prefs(monkeypatch):
    """Mock the shared node/egress probe so the test never touches the network."""
    monkeypatch.setattr(
        "register_core.util.proxy.preflight_nodes_for_register",
        lambda extra=None, *, log_fn=None: dict(extra or {}),
    )
    monkeypatch.setattr(
        "register_core.util.proxy.inject_attempt_proxy",
        lambda extra=None, *, log_fn=None: {**(extra or {}), "proxy": None},
    )
    monkeypatch.setattr(
        "register_core.util.proxy.report_attempt_proxy_result",
        lambda *a, **k: None,
    )


def test_registry_returns_real_outlook_provider(monkeypatch):
    """The control-plane argv `--provider outlook` resolves, through the same
    registry the CLI / Pipeline.from_job uses, to a real OutlookProvider (not a
    fake, not None). This is the first hop of reachability."""
    monkeypatch.delenv("GROK_REGISTER_OUTLOOK_LIVE", raising=False)
    provider = get_provider("outlook")
    assert isinstance(provider, OutlookProvider)
    assert provider.name == "outlook"


def test_outlook_gate_off_pipeline_classifies_provider_and_stops_fast(
    monkeypatch, tmp_path
):
    """Gate OFF + fail_fast=True → the pipeline does exactly ONE attempt, the
    real OutlookProvider short-circuits at the gate (no browser/mailbox), and
    the result is classified as a terminal ``provider`` failure (not mail_miss,
    not fatal). This is the safe control-flow reachability contract: a non-live
    harness never opens a browser/mailbox/proxy and surfaces a provider-kind
    failure to the control plane."""
    monkeypatch.delenv("GROK_REGISTER_OUTLOOK_LIVE", raising=False)
    monkeypatch.setenv("OUTLOOK_AUTHS_DIR", str(tmp_path / "outlook_auths"))
    _mock_proxy_prefs(monkeypatch)

    provider = get_provider("outlook")
    emitted: list = []
    stats = Pipeline(
        provider,
        fail_fast=True,
        on_result=emitted.append,
    ).run(count=3, extra={})

    # Exactly one attempt before fail-fast stop (gate is terminal provider, not
    # a soft mail_miss retry).
    assert len(stats.results) == 1, stats.results
    result = stats.results[0]
    assert result.ok is False
    assert result.error_kind == "provider"
    assert result.provider == "outlook"
    assert result.error is not None
    assert "live gate" in result.error.lower() or "GROK_REGISTER_OUTLOOK_LIVE" in result.error, (
        result.error
    )
    assert stats.fail == 1
    assert stats.ok == 0
    assert stats.stopped_reason, "fail_fast=True must record why it stopped"
    # No artifact written (gate is OFF).
    auths = tmp_path / "outlook_auths"
    assert not auths.exists() or not list(auths.glob("outlook-*.json"))
    # Public view carries no real credential even on a gated failure: the
    # password/secret fields are present (key names are public schema, not
    # secrets) but MUST be empty for a gate-off short-circuit — no account
    # was created, so there is no value to leak.
    public = result.to_public_dict()
    assert public.get("password") in (None, "")
    assert public.get("secret") in (None, "")
    assert public.get("secret_kind") in (None, "none")


def test_outlook_gate_off_is_deterministic_across_attempts(monkeypatch, tmp_path):
    """Gate OFF + fail_fast=False → every attempt across count=3 short-circuits
    identically and deterministically (stateless gate). Proves the live gate is
    not stateful and does not degrade into a network call on repeat attempts."""
    monkeypatch.delenv("GROK_REGISTER_OUTLOOK_LIVE", raising=False)
    monkeypatch.setenv("OUTLOOK_AUTHS_DIR", str(tmp_path / "outlook_auths"))
    _mock_proxy_prefs(monkeypatch)

    provider = get_provider("outlook")
    stats = Pipeline(provider, fail_fast=False).run(count=3, extra={})

    assert len(stats.results) == 3, stats.results
    assert stats.fail == 3
    assert stats.ok == 0
    assert all(r.ok is False for r in stats.results)
    assert all(r.error_kind == "provider" for r in stats.results)
    assert all(
        r.error is not None
        and ("live gate" in r.error.lower() or "GROK_REGISTER_OUTLOOK_LIVE" in r.error)
        for r in stats.results
    )
    # Gate OFF across all three attempts → never opens a browser, never writes.
    auths = tmp_path / "outlook_auths"
    assert not auths.exists() or not list(auths.glob("outlook-*.json"))
