import asyncio
import json
import stat
from pathlib import Path

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


def test_disabled_provider_never_allocates_mailbox(monkeypatch):
    monkeypatch.delenv("GROK_REGISTER_OUTLOOK_LIVE", raising=False)

    class _Source:
        def allocate(self):
            raise AssertionError("must not allocate while live gate is off")

    result = OutlookProvider(config={}).register_one(
        email_source=_Source(), extra={}
    )
    assert result.ok is False
    assert result.error_kind == "provider"


# --- Orchestration-level tests with browser/flow/oauth stubbed ----------------
#
# These exercise _register_one_async end-to-end (recovery release, artifact
# sink) WITHOUT patchright: each collaborator is replaced in the ADAPTER module
# namespace, and OutlookBrowser.open yields a fake session whose context manager
# never imports the browser stack.  All values below are synthetic.

from contextlib import asynccontextmanager  # noqa: E402

from register_core.providers import outlook_adapter as adapter_mod  # noqa: E402
from register_core.providers.outlook_browser import (  # noqa: E402
    OutlookBrowser as _RealOutlookBrowser,
    OutlookRegistrationFlow as _RealFlow,
    RegistrationPageResult,
    OutlookBrowserConfig,
)
from register_core.providers.outlook_oauth import (  # noqa: E402
    OAuthStateMachine as _RealOAuth,
    OAuthTokenResult,
    OutlookOAuthConfig,
)
from register_core.providers.outlook_recovery import RecoverySession  # noqa: E402


class _FakeSession:
    def __init__(self):
        self.page = type("Page", (), {"url": "https://login.live.com/"})()
        self.browser = object()


class _FakeSource:
    name = "cf_temp"

    def __init__(self):
        self.released = []

    def release(self, mailbox, *, success):
        self.released.append((mailbox, success))


def _install_fake_browser(monkeypatch, session):
    class _FakeBrowser:
        def __init__(self, config):
            self.config = config

        @asynccontextmanager
        async def open(self, proxy=None):
            yield session

    monkeypatch.setattr(adapter_mod, "OutlookBrowser", _FakeBrowser)


def _install_fake_flow(monkeypatch, registration_result):
    class _FakeFlow:
        async def register(self, page, email, password, *, captcha_strategy):
            return registration_result

    monkeypatch.setattr(adapter_mod, "OutlookRegistrationFlow", _FakeFlow)


def _install_fake_recovery(monkeypatch, mailbox_address, bound, source):
    recovery = RecoverySession(
        mailbox=type("M", (), {"address": mailbox_address})(),
        source=source,
        bound=bound,
    )

    async def _fake_bind(page, source, *, timeout_seconds=90):
        return recovery

    monkeypatch.setattr(adapter_mod, "bind_recovery_email", _fake_bind)
    return recovery


def _install_fake_oauth(monkeypatch, result):
    class _FakeOAuth:
        def __init__(self, *, config=None):
            self.config = config

        async def run(self, page, email, password, *, proxy="", recovery_session=None, timeout_s=20.0):
            return result

    monkeypatch.setattr(adapter_mod, "OAuthStateMachine", _FakeOAuth)


def _install_fake_temp_source(monkeypatch):
    class _FakeTemp:
        @classmethod
        def from_options(cls, opts):
            return cls()

    monkeypatch.setattr(adapter_mod, "TempMailSource", _FakeTemp)


def _orchestration_provider(tmp_path):
    return OutlookProvider(
        config={
            "outlook_auths_dir": str(tmp_path / "auths"),
            "bind_recovery_email": True,
            "captcha_strategy": 0,
        }
    )


def test_releases_recovery_mailbox_on_oauth_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    _install_fake_browser(monkeypatch, _FakeSession())
    _install_fake_flow(
        monkeypatch, RegistrationPageResult(ok=True)  # no captcha, form advanced
    )
    source = _FakeSource()
    _install_fake_recovery(
        monkeypatch,
        mailbox_address="recovery@invalid",
        bound=True,
        source=source,
    )
    _install_fake_oauth(
        monkeypatch,
        OAuthTokenResult(
            ok=False,
            refresh_token="",
            error_kind="token",
            state="exchange_rejected",
        ),
    )
    _install_fake_temp_source(monkeypatch)

    provider = _orchestration_provider(tmp_path)
    result = provider.register_one(
        email_source=None, extra={"proxy": "http://synthetic.invalid"}
    )

    assert result.ok is False
    assert result.error_kind == "token"
    # The finally block releases the recovery mailbox with success=False.
    assert len(source.released) == 1
    _mailbox, success = source.released[0]
    assert success is False
    # No artifact written on the failure path.
    assert list((tmp_path / "auths").glob("*.json")) == []


def test_success_writes_0600_artifact_and_redacts_public(monkeypatch, tmp_path):
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    _install_fake_browser(monkeypatch, _FakeSession())
    _install_fake_flow(monkeypatch, RegistrationPageResult(ok=True))
    source = _FakeSource()
    _install_fake_recovery(
        monkeypatch,
        mailbox_address="recovery@invalid",
        bound=True,
        source=source,
    )
    _install_fake_oauth(
        monkeypatch,
        OAuthTokenResult(
            ok=True,
            refresh_token="synthetic-refresh",
            state="refresh_token",
        ),
    )
    _install_fake_temp_source(monkeypatch)

    provider = _orchestration_provider(tmp_path)
    result = provider.register_one(
        email_source=None, extra={"proxy": "http://synthetic.invalid"}
    )

    assert result.ok is True
    assert result.provider == "outlook"
    assert result.secret_kind == "refresh_token"
    assert result.secret == "synthetic-refresh"

    artifact = Path(result.artifacts["outlook_auth_path"])
    assert artifact.exists()
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    body = json.loads(artifact.read_text(encoding="utf-8"))
    assert body["refresh_token"] == "synthetic-refresh"
    assert body["bound"] is True
    assert body["recovery_email"] == "recovery@invalid"
    assert "synthetic-refresh" not in json.dumps(result.to_public_dict())
    assert source.released and source.released[0][1] is True


def _install_oauth_that_raises(monkeypatch, exc):
    """OAuth stub whose run() raises — simulating a terminal token rejection
    (ValueError, e.g. missing_refresh_token after a burnt auth code) or a
    typed register-core error the pipeline must classify upstream."""

    class _RaisingOAuth:
        def __init__(self, *, config=None):
            self.config = config

        async def run(self, page, email, password, *, proxy="", recovery_session=None, timeout_s=20.0):
            raise exc

    monkeypatch.setattr(adapter_mod, "OAuthStateMachine", _RaisingOAuth)


def test_oauth_value_error_is_classified_as_token_not_raised(monkeypatch, tmp_path):
    """A ValueError from the OAuth state machine (e.g. missing_refresh_token)
    is a terminal token rejection, NOT a RegisterCoreError. Without the
    classifier it would escape the pipeline's typed handlers and abort the
    batch. register_one must convert it to error_kind="token"."""
    import pytest

    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    _install_fake_browser(monkeypatch, _FakeSession())
    _install_fake_flow(monkeypatch, RegistrationPageResult(ok=True))
    source = _FakeSource()
    _install_fake_recovery(
        monkeypatch, mailbox_address="recovery@invalid", bound=True, source=source
    )
    _install_oauth_that_raises(monkeypatch, ValueError("missing_refresh_token"))
    _install_fake_temp_source(monkeypatch)

    provider = _orchestration_provider(tmp_path)
    result = provider.register_one(
        email_source=None, extra={"proxy": "http://synthetic.invalid"}
    )

    assert result.ok is False
    assert result.error_kind == "token"
    assert "missing_refresh_token" in (result.error or "")
    # No artifact on the failure path.
    assert list((tmp_path / "auths").glob("*.json")) == []
    # Recovery mailbox still released with success=False in the finally.
    assert source.released and source.released[0][1] is False


def test_register_core_error_propagates_for_pipeline_classification(monkeypatch, tmp_path):
    """MailMissError / FailFastError / ProviderError are RegisterCoreError
    subclasses the PIPELINE classifies (mail_miss retry, fatal stop, provider
    terminal). register_one must NOT swallow them — if it returned a plain
    RegisterResult the pipeline would lose the mail-miss retry classification.
    They propagate (here via asyncio.run re-raise); the ValueError classifier
    must not intercept them."""
    import pytest

    from register_core.errors import MailMissError, ProviderError

    for exc, _label in (
        (ProviderError("recovery_submit_failed"), "provider"),
        (MailMissError("mail_miss"), "mail_miss"),
    ):
        monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
        _install_fake_browser(monkeypatch, _FakeSession())
        _install_fake_flow(monkeypatch, RegistrationPageResult(ok=True))
        source = _FakeSource()
        _install_fake_recovery(
            monkeypatch, mailbox_address="recovery@invalid", bound=True, source=source
        )
        # Recovery bind happens before oauth.run; raise from recovery instead
        # so the typed error originates from the orchestration path.

        async def _raising_bind(page, source_arg, *, timeout_seconds=90):
            raise exc

        monkeypatch.setattr(adapter_mod, "bind_recovery_email", _raising_bind)
        _install_fake_temp_source(monkeypatch)

        provider = _orchestration_provider(tmp_path)
        with pytest.raises(type(exc)):
            provider.register_one(
                email_source=None, extra={"proxy": "http://synthetic.invalid"}
            )
        # Still no artifact written when it raises.
        assert list((tmp_path / "auths").glob("*.json")) == []


def test_fun_captcha_failure_releases_recovery_never_allocated(monkeypatch, tmp_path):
    """FunCaptcha is detected before recovery binding, so no mailbox to release."""
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    _install_fake_browser(monkeypatch, _FakeSession())
    _install_fake_flow(
        monkeypatch,
        RegistrationPageResult(
            ok=False, error_kind="captcha", error="FunCaptcha",
            fun_captcha_seen=True,
        ),
    )

    class _ExplodingSource:
        def release(self, mailbox, *, success):
            raise AssertionError("no mailbox allocated; release must not run")

    monkeypatch.setattr(
        adapter_mod, "bind_recovery_email",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not bind")),
    )
    _install_fake_temp_source(monkeypatch)

    provider = _orchestration_provider(tmp_path)
    result = provider.register_one(
        email_source=None, extra={"proxy": "http://synthetic.invalid"}
    )
    assert result.ok is False
    assert result.error_kind == "captcha"
    assert list((tmp_path / "auths").glob("*.json")) == []


def test_captcha_bridge_strategy2_manual_handoff(monkeypatch, tmp_path):
    """captcha_frame_seen + strategy 2 → manual handoff, no bridge call."""
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    _install_fake_browser(monkeypatch, _FakeSession())
    _install_fake_flow(
        monkeypatch,
        RegistrationPageResult(
            ok=False, error_kind="captcha", error="manual",
            captcha_frame_seen=True,
        ),
    )

    async def _no_solve(*a, **k):
        raise AssertionError("manual handoff must not invoke the bridge")

    class _NoBridge:
        async def solve(self, *a, **k):
            return await _no_solve(*a, **k)

    monkeypatch.setattr(adapter_mod, "OutlookCaptchaBridge", _NoBridge)
    _install_fake_recovery(
        monkeypatch,
        mailbox_address="recovery@invalid", bound=True, source=_FakeSource(),
    )
    provider = OutlookProvider(
        config={
            "outlook_auths_dir": str(tmp_path / "auths"),
            "bind_recovery_email": False,
            "captcha_strategy": 2,
        }
    )
    result = provider.register_one(
        email_source=None, extra={"proxy": "http://synthetic.invalid"}
    )
    assert result.ok is False
    assert result.error_kind == "captcha"
    assert result.error == "manual_handoff"
    assert result.artifacts.get("outlook_steps") == ["register"]
