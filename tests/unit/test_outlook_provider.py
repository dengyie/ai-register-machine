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


def test_config_value_error_is_classified_as_provider_not_token(monkeypatch, tmp_path):
    """A ValueError raised by config validation (e.g. bad captcha_strategy in
    schema.outlook_options) is a provider/config problem, NOT a terminal token
    rejection. The outer except ValueError must NOT bucket it as 'token' — ops
    must see the real provider bucket. Only the missing_refresh_token sentinel
    is token. (Regression for the prior blanket ValueError->token.)"""
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    provider = OutlookProvider(
        config={
            "outlook_auths_dir": str(tmp_path / "auths"),
            "bind_recovery_email": True,
            "captcha_strategy": 7,  # invalid -> ValueError inside _register_one_async
        }
    )
    result = provider.register_one(
        email_source=None, extra={"proxy": "http://synthetic.invalid"}
    )
    assert result.ok is False
    assert result.error_kind == "provider"
    assert "captcha_strategy" in (result.error or "")


def test_runtime_error_with_credentialed_proxy_is_scrubbed(monkeypatch, tmp_path):
    """If a patchright/Chromium launch failure embeds the credentialed proxy URL
    into the RuntimeError text, RegisterResult.error must NOT carry the proxy
    username/password. Defense-in-depth for cannot-verify-from-diff finding."""
    proxy_with_creds = "http://u:sekret@10.0.0.5:7890"
    leaked_format = "patchright launch failed: server " + proxy_with_creds + " refused"

    class _BoomBrowser:
        def __init__(self, config):
            self.config = config

        @asynccontextmanager
        async def open(self, proxy=None):
            raise RuntimeError(leaked_format)
            yield  # noqa: unreachable

    monkeypatch.setattr(adapter_mod, "OutlookBrowser", _BoomBrowser)
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    provider = OutlookProvider(
        config={"outlook_auths_dir": str(tmp_path / "auths")}
    )
    result = provider.register_one(
        email_source=None, extra={"proxy": proxy_with_creds}
    )
    assert result.ok is False
    assert result.error_kind == "provider"
    err = result.error or ""
    assert "sekret" not in err, err
    assert proxy_with_creds not in err, err
    # Host is still diagnosable; only userinfo was redacted.
    assert "10.0.0.5:7890" in err, err


def test_value_error_with_credentialed_proxy_is_scrubbed(monkeypatch, tmp_path):
    """Same scrub applies on the ValueError path."""
    proxy_with_creds = "https://alice:hunter2@gw.example:443"

    class _BoomBrowser:
        def __init__(self, config):
            self.config = config

        @asynccontextmanager
        async def open(self, proxy=None):
            raise ValueError("bad config near " + proxy_with_creds)
            yield  # noqa: unreachable

    monkeypatch.setattr(adapter_mod, "OutlookBrowser", _BoomBrowser)
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    provider = OutlookProvider(
        config={"outlook_auths_dir": str(tmp_path / "auths")}
    )
    result = provider.register_one(
        email_source=None, extra={"proxy": proxy_with_creds}
    )
    assert result.ok is False
    assert result.error_kind == "provider"  # not the token sentinel
    err = result.error or ""
    assert "hunter2" not in err, err
    assert proxy_with_creds not in err, err


def test_register_core_error_propagates_for_pipeline_classification(monkeypatch, tmp_path):
    """MailMissError / FailFastError / ProviderError are RegisterCoreError
    subclasses the PIPELINE classifies (mail_miss retry, fatal stop, provider
    terminal). register_one must NOT swallow them — if it returned a plain
    RegisterResult the pipeline would lose the mail-miss retry classification.
    They propagate (here via asyncio.run re-raise); the ValueError classifier
    must not intercept them."""
    import pytest

    from register_core.errors import FailFastError, MailMissError, ProviderError

    for exc, _label in (
        (ProviderError("recovery_submit_failed"), "provider"),
        (MailMissError("mail_miss"), "mail_miss"),
        (FailFastError("captcha_infra_dead"), "fatal"),
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


def test_captcha_bridge_strategy2_manual_handoff_headless_returns_immediately(
    monkeypatch, tmp_path
):
    """captcha_frame_seen + strategy 2 + headless → manual_handoff, no wait, no bridge."""
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
            "headless": True,
        }
    )
    result = provider.register_one(
        email_source=None, extra={"proxy": "http://synthetic.invalid"}
    )
    assert result.ok is False
    assert result.error_kind == "captcha"
    assert result.error == "manual_handoff"
    assert result.artifacts.get("outlook_steps") == ["register"]


# --- strategy 2 interactive manual-wait (headed) -----------------------------


class _CountdownLocator:
    """count() returns from a scripted sequence, then stays at the last value.

    Simulates the captcha iframe detaching after the operator solves it: each
    poll consumes the next value; when the script is exhausted the locator
    stays detached (0).
    """

    def __init__(self, counts):
        self._counts = list(counts)

    async def count(self):
        if len(self._counts) > 1:
            return self._counts.pop(0)
        return self._counts[0]


class _HandoffPage:
    """Page stub for manual_handoff_wait: only the two captcha selectors matter."""

    def __init__(self, enforcement_counts, challenge_counts):
        self._enforcement = _CountdownLocator(enforcement_counts)
        self._challenge = _CountdownLocator(challenge_counts)

    def locator(self, selector):
        if selector == "iframe#enforcementFrame":
            return self._enforcement
        if 'title="验证质询"' in selector:
            return self._challenge
        raise AssertionError(f"unexpected selector {selector}")


def test_manual_handoff_wait_returns_true_when_iframes_detach():
    from register_core.providers.outlook_adapter import manual_handoff_wait

    # enforcement: present, present, gone. challenge: present, gone, gone.
    page = _HandoffPage([1, 1, 0], [1, 0, 0])
    cleared = asyncio.run(
        manual_handoff_wait(page, timeout_s=10.0, poll_s=0.01)
    )
    assert cleared is True


def test_manual_handoff_wait_times_out_when_iframe_stays():
    from register_core.providers.outlook_adapter import manual_handoff_wait

    page = _HandoffPage([1], [0])
    cleared = asyncio.run(
        manual_handoff_wait(page, timeout_s=0.05, poll_s=0.01)
    )
    assert cleared is False


def test_strategy2_headed_waits_then_proceeds_to_oauth(monkeypatch, tmp_path):
    """headed + strategy 2 + captcha → wait (iframe detaches) → OAuth runs →
    artifact written. This is the live operator path: solve the FunCaptcha in
    the open browser window, registration completes automatically."""
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    _install_fake_browser(monkeypatch, _FakeSession())
    _install_fake_flow(
        monkeypatch,
        RegistrationPageResult(
            ok=False, error_kind="captcha", error="manual handoff",
            captcha_frame_seen=True,
        ),
    )

    async def _wait_succeeds(page, *, timeout_s, poll_s=2.0):
        return True

    monkeypatch.setattr(adapter_mod, "manual_handoff_wait", _wait_succeeds)
    _install_fake_oauth(
        monkeypatch,
        OAuthTokenResult(ok=True, refresh_token="synthetic-rt", state="refresh_token"),
    )
    _install_fake_temp_source(monkeypatch)

    provider = OutlookProvider(
        config={
            "outlook_auths_dir": str(tmp_path / "auths"),
            "bind_recovery_email": False,
            "captcha_strategy": 2,
            "headless": False,
        }
    )
    result = provider.register_one(
        email_source=None, extra={"proxy": "http://synthetic.invalid"}
    )
    assert result.ok is True
    assert result.secret == "synthetic-rt"
    assert result.secret_kind == "refresh_token"
    artifact = Path(result.artifacts["outlook_auth_path"])
    assert artifact.exists()
    body = json.loads(artifact.read_text(encoding="utf-8"))
    assert body["refresh_token"] == "synthetic-rt"
    assert result.artifacts["outlook_steps"] == [
        "register", "manual_captcha", "oauth",
    ]


def test_strategy2_headed_wait_timeout_returns_manual_handoff_timeout(
    monkeypatch, tmp_path
):
    """headed + strategy 2 + captcha, operator never solves → bounded timeout
    failure, no OAuth call, no artifact."""
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    _install_fake_browser(monkeypatch, _FakeSession())
    _install_fake_flow(
        monkeypatch,
        RegistrationPageResult(
            ok=False, error_kind="captcha", error="manual handoff",
            captcha_frame_seen=True,
        ),
    )

    async def _wait_times_out(page, *, timeout_s, poll_s=2.0):
        return False

    monkeypatch.setattr(adapter_mod, "manual_handoff_wait", _wait_times_out)

    class _NoOAuth:
        def __init__(self, *, config=None):
            pass

        async def run(self, *a, **k):
            raise AssertionError("OAuth must not run after handoff timeout")

    monkeypatch.setattr(adapter_mod, "OAuthStateMachine", _NoOAuth)
    _install_fake_temp_source(monkeypatch)

    provider = OutlookProvider(
        config={
            "outlook_auths_dir": str(tmp_path / "auths"),
            "bind_recovery_email": False,
            "captcha_strategy": 2,
            "headless": False,
        }
    )
    result = provider.register_one(
        email_source=None, extra={"proxy": "http://synthetic.invalid"}
    )
    assert result.ok is False
    assert result.error_kind == "captcha"
    assert result.error == "manual_handoff_timeout"
    assert result.artifacts.get("outlook_steps") == ["register", "manual_captcha"]
    assert list((tmp_path / "auths").glob("*.json")) == []


def test_fun_captcha_headed_also_waits_for_operator(monkeypatch, tmp_path):
    """FunCaptcha (enforcementFrame) is the captcha Outlook actually shows; in
    headed mode the operator solves it in-window, so the wait must cover it
    too — not just the 验证质询 iframe variant."""
    monkeypatch.setenv("GROK_REGISTER_OUTLOOK_LIVE", "1")
    _install_fake_browser(monkeypatch, _FakeSession())
    _install_fake_flow(
        monkeypatch,
        RegistrationPageResult(
            ok=False, error_kind="captcha", error="FunCaptcha enforcement frame",
            fun_captcha_seen=True,
        ),
    )

    async def _wait_succeeds(page, *, timeout_s, poll_s=2.0):
        return True

    monkeypatch.setattr(adapter_mod, "manual_handoff_wait", _wait_succeeds)
    _install_fake_oauth(
        monkeypatch,
        OAuthTokenResult(ok=True, refresh_token="synthetic-rt", state="refresh_token"),
    )
    _install_fake_temp_source(monkeypatch)

    provider = OutlookProvider(
        config={
            "outlook_auths_dir": str(tmp_path / "auths"),
            "bind_recovery_email": False,
            "captcha_strategy": 2,
            "headless": False,
        }
    )
    result = provider.register_one(
        email_source=None, extra={"proxy": "http://synthetic.invalid"}
    )
    assert result.ok is True
    assert result.secret == "synthetic-rt"
