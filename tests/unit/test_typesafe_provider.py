#!/usr/bin/env python3
"""Offline unit tests for the typesafe.ai provider (no live register)."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from register_core.contracts import Mailbox, OtpCode, RegisterResult
from register_core.decode.extract import extract_typesafe_magic_link
from register_core.errors import FailFastError, MailMissError
from register_core.pipeline import Pipeline
from register_core.providers.registry import get_provider, list_providers
from register_core.providers.typesafe_adapter import TypesafeProvider
from register_core.verify.registry import get_verifier
from register_core.verify.typesafe_key import TypesafeKeyVerifier


MAGIC_LINK = (
    "https://login.typesafe.ai/v1/magic_links/redirect"
    "?public_token=pub-test-token"
    "&stytch_token_type=magic_links&token=stytch-secret-token"
)

LOGIN_HTML = """
<html><body>
<input type="hidden" name="$ACTION_3:0" value="{&quot;id&quot;:&quot;act-abc&quot;}" />
<input type="hidden" name="$ACTION_3:2" value="encrypted-blob-xyz" />
<input type="email" />
<script>self.__next_f.push([1,"dpl=cc6f6dca06537cc04123caaaf50ca5a76d506a92"])</script>
</body></html>
"""

LOGIN_HTML_NEW_DPL = LOGIN_HTML.replace(
    "cc6f6dca06537cc04123caaaf50ca5a76d506a92",
    "ffffffffffffffffffffffffffffffffffffffff",
)


class FakeEmail:
    name = "fake"

    def __init__(self, *, fail_otp: bool = False, link: str = MAGIC_LINK) -> None:
        self.fail_otp = fail_otp
        self.link = link
        self.released: list[tuple[str, bool]] = []
        self.poll_kwargs: list[dict[str, Any]] = []
        self.allocated = 0
        self._lock = threading.Lock()

    def allocate(self) -> Mailbox:
        with self._lock:
            self.allocated += 1
        return Mailbox(
            address="newuser@example.com",
            token="t",
            provider=self.name,
            meta={"local": "newuser", "domain": "example.com"},
        )

    def poll_otp(self, mailbox: Mailbox, **kwargs: Any) -> OtpCode:
        with self._lock:
            self.poll_kwargs.append(dict(kwargs))
        if self.fail_otp:
            raise MailMissError("magic link timeout test")
        return OtpCode(code=self.link, source=self.name)

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        with self._lock:
            self.released.append((mailbox.address, success))


class TestTypesafeRegistry(unittest.TestCase):
    def test_registered_and_aliases(self):
        self.assertIn("typesafe", list_providers())
        self.assertEqual(get_provider("jev").name, "typesafe")
        self.assertEqual(get_provider("typesafe-ai").name, "typesafe")
        v = get_verifier("typesafe")
        self.assertEqual(v.name, "typesafe_key")
        self.assertEqual(get_verifier("jev").name, "typesafe_key")


class TestTypesafeVerifier(unittest.TestCase):
    def test_shape_gate(self):
        v = TypesafeKeyVerifier(live=False)
        bad = v.verify(RegisterResult(ok=True, provider="typesafe", secret="short"))
        self.assertFalse(bad.ok)
        good = v.verify(
            RegisterResult(
                ok=True,
                provider="typesafe",
                secret="ts_" + "x" * 24,
                secret_kind="api_key",
            )
        )
        self.assertTrue(good.ok)
        wrong_kind = v.verify(
            RegisterResult(
                ok=True,
                provider="typesafe",
                secret="ts_" + "y" * 24,
                secret_kind="refresh_token",
            )
        )
        self.assertFalse(wrong_kind.ok)
        live = TypesafeKeyVerifier(live=True)
        live_ok = live.verify(
            RegisterResult(
                ok=True,
                provider="typesafe",
                secret="ts_" + "x" * 24,
                secret_kind="api_key",
            )
        )
        self.assertTrue(live_ok.ok)
        self.assertIn("shape_ok", live_ok.detail)


class TestTypesafeExtract(unittest.TestCase):
    def test_extract_plain_and_html_entity(self):
        parsed = extract_typesafe_magic_link(MAGIC_LINK)
        self.assertEqual(parsed["token"], "stytch-secret-token")
        self.assertEqual(parsed["public_token"], "pub-test-token")
        encoded = MAGIC_LINK.replace("&", "&amp;")
        parsed2 = extract_typesafe_magic_link(f"<a href=\"{encoded}\">login</a>")
        self.assertEqual(parsed2["token"], "stytch-secret-token")
        self.assertEqual(extract_typesafe_magic_link("no link here"), {})
        reordered = (
            "https://login.typesafe.ai/v1/magic_links/redirect"
            "?token=stytch-secret-token&stytch_token_type=magic_links"
            "&public_token=pub-test-token"
        )
        parsed3 = extract_typesafe_magic_link(reordered)
        self.assertEqual(parsed3["token"], "stytch-secret-token")
        self.assertEqual(parsed3["public_token"], "pub-test-token")

    def test_parse_login_action_and_deployment_mismatch(self):
        from providers.typesafe.protocol.constants import CONSOLE_DEPLOYMENT_ID
        from providers.typesafe.protocol.flow import (
            parse_deployment_id,
            parse_login_action,
        )

        action = parse_login_action(LOGIN_HTML)
        self.assertEqual(action["action_id"], "act-abc")
        self.assertEqual(action["index"], "3")
        self.assertEqual(action["blob"], "encrypted-blob-xyz")
        self.assertEqual(parse_deployment_id(LOGIN_HTML), CONSOLE_DEPLOYMENT_ID)
        self.assertNotEqual(parse_deployment_id(LOGIN_HTML_NEW_DPL), CONSOLE_DEPLOYMENT_ID)

    def test_parse_login_action_missing_includes_snippet(self):
        from providers.typesafe.protocol.flow import TypesafeRegisterError, parse_login_action

        with self.assertRaises(TypesafeRegisterError) as ctx:
            parse_login_action("<html><body>no action fields</body></html>")
        self.assertEqual(ctx.exception.kind, "session")
        snippet = (ctx.exception.steps.get("login_action") or {}).get("html_snippet") or ""
        self.assertIn("no action fields", snippet)
        self.assertLessEqual(len(snippet), 400)

    def test_result_public_redacts(self):
        from providers.typesafe.protocol.flow import TypesafeResult

        r = TypesafeResult(
            ok=True,
            email="e@x.com",
            api_key="sk-live-abcdefghijklmnopqrstuvwxyz",
            api_key_id="key-1",
        )
        blob = json.dumps(r.to_public_dict())
        self.assertNotIn("sk-live-abcdefghijklmnopqrstuvwxyz", blob)


class TestTypesafeAdapterAttribution(unittest.TestCase):
    def test_success_from_this_run_only(self):
        email = FakeEmail()
        provider = TypesafeProvider(proxy="")
        from providers.typesafe.protocol.flow import TypesafeResult

        fake_ok = TypesafeResult(
            ok=True,
            email="newuser@example.com",
            api_key="this-run-api-key-should-win",
            api_key_id="kid-1",
            user_id="u1",
            organization_id="org-1",
            steps={"login_action": {}, "api_key": {}},
        )
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            with patch(
                "register_core.providers.typesafe_adapter.OUTPUT_DIR", out
            ), patch(
                "providers.typesafe.protocol.flow.register_one",
                return_value=fake_ok,
            ):
                (out / "accounts.jsonl").write_text(
                    json.dumps(
                        {
                            "ok": True,
                            "email": "old@example.com",
                            "api_key": "old-api-key-should-not-win",
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                result = provider.register_one(
                    email_source=email, extra={"skip_console_probe": True}
                )
                ledger = (out / "accounts.jsonl").read_text(encoding="utf-8")
                auth_files = list(out.glob("typesafe-*.json"))
                self.assertEqual(len(auth_files), 1)
                written = json.loads(auth_files[0].read_text(encoding="utf-8"))
                self.assertEqual(written["api_key"], "this-run-api-key-should-win")
                # Historical ledger is left untouched; this-run json is the deliverable.
                self.assertNotIn("this-run-api-key-should-win", ledger)
                self.assertEqual(ledger.count("old-api-key-should-not-win"), 1)

        self.assertTrue(result.ok)
        self.assertEqual(result.email, "newuser@example.com")
        self.assertEqual(result.secret, "this-run-api-key-should-win")
        self.assertEqual(result.secret_kind, "api_key")
        self.assertNotEqual(result.secret, "old-api-key-should-not-win")
        pub = result.to_public_dict()
        self.assertNotIn("this-run-api-key-should-win", json.dumps(pub))
        self.assertEqual(email.released, [("newuser@example.com", True)])

    def test_magic_link_provider_uses_typesafe_sender_hint(self):
        email = FakeEmail()
        provider = TypesafeProvider(proxy="")
        captured: list[str] = []

        def fake_register_one(**kwargs: Any):
            fn = kwargs.get("magic_link_provider")
            assert callable(fn)
            captured.append(fn())
            from providers.typesafe.protocol.flow import TypesafeResult

            return TypesafeResult(
                ok=True,
                email="newuser@example.com",
                api_key="this-run-api-key-should-win",
                api_key_id="kid-1",
            )

        with tempfile.TemporaryDirectory() as td, patch(
            "register_core.providers.typesafe_adapter.OUTPUT_DIR", Path(td)
        ), patch(
            "providers.typesafe.protocol.flow.register_one",
            side_effect=fake_register_one,
        ):
            result = provider.register_one(
                email_source=email, extra={"skip_console_probe": True}
            )
        self.assertTrue(result.ok)
        self.assertEqual(captured, [MAGIC_LINK])
        self.assertEqual(email.poll_kwargs[0].get("sender_hint"), "typesafe")

    def test_missing_api_key_is_failure(self):
        email = FakeEmail()
        provider = TypesafeProvider(proxy="")
        from providers.typesafe.protocol.flow import TypesafeResult

        fake = TypesafeResult(ok=True, email="newuser@example.com", api_key="")
        with patch(
            "providers.typesafe.protocol.flow.register_one", return_value=fake
        ):
            result = provider.register_one(
                email_source=email, extra={"skip_console_probe": True}
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "token")
        self.assertEqual(result.artifacts.get("fail_step"), "api_key")
        self.assertEqual(email.released[-1][1], False)

    def test_mail_miss_returns_result_with_email(self):
        email = FakeEmail(fail_otp=True)
        provider = TypesafeProvider(proxy="")
        from providers.typesafe.protocol.flow import TypesafeRegisterError

        with patch(
            "providers.typesafe.protocol.flow.register_one",
            side_effect=TypesafeRegisterError("wait:x", kind="mail_miss"),
        ):
            result = provider.register_one(
                email_source=email, extra={"skip_console_probe": True}
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "mail_miss")
        self.assertEqual(result.email, "newuser@example.com")
        self.assertEqual(email.released[-1][1], False)
        # No MailMissError on the chain: do not borrow a neighbor's diag.
        self.assertEqual(
            (result.artifacts.get("otp_wait") or {}).get("notes"),
            "diagnostics_unavailable",
        )

    def test_mail_miss_keeps_this_attempt_diagnostics(self):
        from register_core.contracts import OtpWaitDiagnostics
        from providers.typesafe.protocol.flow import TypesafeRegisterError

        email = FakeEmail()
        email.last_wait_diagnostics = OtpWaitDiagnostics(
            elapsed_seconds=19.48,
            poll_count=7,
            message_scan_count=1,
            failure_class="no_mail",
        )
        own = OtpWaitDiagnostics(
            elapsed_seconds=4.1,
            poll_count=2,
            message_scan_count=1,
            failure_class="parse_fail",
        )
        provider = TypesafeProvider(proxy="")

        def raise_with_cause(*_a: Any, **_k: Any):
            err = TypesafeRegisterError(
                "magic link missing or malformed",
                kind="mail_miss",
                step="wait_magic_link",
            )
            err.__cause__ = MailMissError("malformed", diagnostics=own)
            raise err

        with patch(
            "providers.typesafe.protocol.flow.register_one",
            side_effect=raise_with_cause,
        ):
            result = provider.register_one(
                email_source=email, extra={"skip_console_probe": True}
            )
        wait = result.artifacts.get("otp_wait") or {}
        self.assertEqual(wait.get("elapsed_seconds"), 4.1)
        self.assertEqual(wait.get("failure_class"), "parse_fail")
        self.assertNotEqual(wait.get("elapsed_seconds"), 19.48)

    def test_console_probe_failure_does_not_allocate(self):
        email = FakeEmail()
        provider = TypesafeProvider(proxy="")
        from providers.typesafe.protocol.flow import TypesafeRegisterError

        with patch(
            "providers.typesafe.protocol.flow.probe_console",
            side_effect=TypesafeRegisterError("console down", kind="network", step="console_probe"),
        ), patch(
            "providers.typesafe.protocol.session.create_session",
            return_value=object(),
        ):
            result = provider.register_one(email_source=email)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "network")
        self.assertEqual(result.artifacts.get("fail_step"), "console_probe")
        self.assertEqual(email.allocated, 0)
        self.assertEqual(email.released, [])
        self.assertEqual((result.artifacts.get("console_probe") or {}).get("ok"), False)

    def test_probe_http_5xx_is_network_4xx_is_session(self):
        from providers.typesafe.protocol.flow import TypesafeRegisterError, probe_console

        class Resp:
            def __init__(self, status: int, text: str = "<html>err</html>") -> None:
                self.status_code = status
                self.text = text
                self.headers = {}

        with patch(
            "providers.typesafe.protocol.flow._get",
            return_value=(Resp(502), ""),
        ):
            with self.assertRaises(TypesafeRegisterError) as ctx:
                probe_console(session=object())
        self.assertEqual(ctx.exception.kind, "network")
        self.assertEqual(ctx.exception.step, "console_probe")

        with patch(
            "providers.typesafe.protocol.flow._get",
            return_value=(Resp(403, LOGIN_HTML), ""),
        ):
            with self.assertRaises(TypesafeRegisterError) as ctx403:
                probe_console(session=object())
        self.assertEqual(ctx403.exception.kind, "session")

    def test_register_one_reuses_probe_action(self):
        from providers.typesafe.protocol.flow import TypesafeResult, register_one

        action = {
            "action_id": "act-abc",
            "index": "3",
            "blob": "encrypted-blob-xyz",
            "deployment_id": "ffffffffffffffffffffffffffffffffffffffff",
            "deployment_mismatch": True,
        }
        captured: dict[str, Any] = {}

        def fake_send(session, email, *, action=None, log=None):
            captured["action"] = dict(action or {})
            return {"x_action_redirect": "/login?sent=true"}

        with patch(
            "providers.typesafe.protocol.flow.login_action",
            side_effect=AssertionError("must reuse probe action"),
        ), patch(
            "providers.typesafe.protocol.flow.send_magic_link",
            side_effect=fake_send,
        ), patch(
            "providers.typesafe.protocol.flow.auth_callback",
            return_value={"userId": "u1", "org_memberships": [{"org": {"id": "o1", "name": "n"}}]},
        ), patch(
            "providers.typesafe.protocol.flow.create_api_key",
            return_value={"api_key": "this-run-api-key-should-win", "id": "kid-1"},
        ):
            result = register_one(
                email="newuser@example.com",
                magic_link_provider=lambda: MAGIC_LINK,
                session=object(),
                action=action,
            )
        self.assertTrue(result.ok)
        self.assertEqual(captured["action"]["action_id"], "act-abc")
        self.assertTrue(result.steps["login_action"]["reused_probe"])
        self.assertTrue(result.deployment_mismatch)

    def test_mail_miss_from_provider_stays_mail_miss(self):
        from providers.typesafe.protocol.flow import TypesafeRegisterError, register_one

        action = {"action_id": "act-abc", "index": "3", "blob": "blob"}

        def boom() -> str:
            raise MailMissError("magic link timeout test")

        with patch(
            "providers.typesafe.protocol.flow.login_action",
            side_effect=AssertionError("reuse"),
        ), patch(
            "providers.typesafe.protocol.flow.send_magic_link",
            return_value={"x_action_redirect": "/login?sent=true"},
        ):
            with self.assertRaises(TypesafeRegisterError) as ctx:
                register_one(
                    email="newuser@example.com",
                    magic_link_provider=boom,
                    session=object(),
                    action=action,
                )
        self.assertEqual(ctx.exception.kind, "mail_miss")
        self.assertEqual(ctx.exception.step, "wait_magic_link")
        self.assertIsInstance(ctx.exception.__cause__, MailMissError)

    def test_adapter_forwards_probe_action(self):
        email = FakeEmail()
        provider = TypesafeProvider(proxy="")
        seen: dict[str, Any] = {}

        def fake_probe(**kwargs: Any):
            return {
                "ok": True,
                "status": 200,
                "index": "3",
                "deployment_id": "dpl",
                "action": {"action_id": "act-abc", "index": "3", "blob": "blob"},
            }

        def fake_register_one(**kwargs: Any):
            seen["action"] = kwargs.get("action")
            from providers.typesafe.protocol.flow import TypesafeResult

            return TypesafeResult(
                ok=True,
                email="newuser@example.com",
                api_key="this-run-api-key-should-win",
                api_key_id="kid-1",
            )

        with tempfile.TemporaryDirectory() as td, patch(
            "register_core.providers.typesafe_adapter.OUTPUT_DIR", Path(td)
        ), patch(
            "providers.typesafe.protocol.flow.probe_console",
            side_effect=fake_probe,
        ), patch(
            "providers.typesafe.protocol.session.create_session",
            return_value=object(),
        ), patch(
            "providers.typesafe.protocol.flow.register_one",
            side_effect=fake_register_one,
        ):
            result = provider.register_one(email_source=email)
        self.assertTrue(result.ok)
        self.assertEqual((seen.get("action") or {}).get("action_id"), "act-abc")
        self.assertEqual(email.allocated, 1)

    def test_pipeline_in_process_accepts_email_source(self):
        from register_core.contracts import RegisterJob

        job = RegisterJob(provider="typesafe", email_source="tinyhost", verify=False)
        pipe = Pipeline.from_job(job)
        self.assertEqual(pipe.provider.name, "typesafe")
        self.assertIsNotNone(pipe.email_source)

    def test_fail_fast_empty_allocate(self):
        class EmptyMail:
            name = "empty"

            def allocate(self) -> Mailbox:
                return Mailbox(address="", provider=self.name)

            def poll_otp(self, mailbox, **kwargs):
                raise AssertionError("no")

            def release(self, mailbox, *, success):
                return None

        with self.assertRaises(FailFastError):
            TypesafeProvider(proxy="").register_one(
                email_source=EmptyMail(), extra={"skip_console_probe": True}
            )

    def test_pipeline_uses_this_run_result(self):
        email = FakeEmail()
        provider = TypesafeProvider(proxy="")
        from providers.typesafe.protocol.flow import TypesafeResult

        fake_ok = TypesafeResult(
            ok=True,
            email="newuser@example.com",
            api_key="pipeline-this-run-key-xx",
            api_key_id="kid",
        )

        def _noop_preflight(extra, **_kw):
            base = dict(extra or {})
            base["_nodes_preflight_done"] = True
            base["_nodes_preflight"] = {"skipped": True, "reason": "test"}
            return base

        def _noop_inject(extra, **_kw):
            return dict(extra or {})

        with tempfile.TemporaryDirectory() as td:
            with (
                patch(
                    "register_core.providers.typesafe_adapter.OUTPUT_DIR", Path(td)
                ),
                patch(
                    "providers.typesafe.protocol.flow.register_one",
                    return_value=fake_ok,
                ),
                patch(
                    "register_core.util.proxy.preflight_nodes_for_register",
                    side_effect=_noop_preflight,
                ),
                patch(
                    "register_core.util.proxy.inject_attempt_proxy",
                    side_effect=_noop_inject,
                ),
            ):
                pipe = Pipeline(
                    provider,
                    email_source=email,
                    verifier=TypesafeKeyVerifier(),
                    fail_fast=True,
                )
                stats = pipe.run(
                    1,
                    extra={
                        "egress": "direct",
                        "nodes_preflight": False,
                        "skip_console_probe": True,
                    },
                )
        self.assertEqual(stats.ok, 1)
        self.assertEqual(stats.fail, 0)
        self.assertTrue(stats.results[0].ok)

    def test_output_filenames_unique_same_second(self):
        email = FakeEmail()
        provider = TypesafeProvider(proxy="")
        from providers.typesafe.protocol.flow import TypesafeResult

        fake_ok = TypesafeResult(
            ok=True,
            email="newuser@example.com",
            api_key="this-run-api-key-should-win",
            api_key_id="kid-1",
        )
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            with patch(
                "register_core.providers.typesafe_adapter.OUTPUT_DIR", out
            ), patch(
                "providers.typesafe.protocol.flow.register_one",
                return_value=fake_ok,
            ):
                a = provider.register_one(
                    email_source=email, extra={"skip_console_probe": True}
                )
                b = provider.register_one(
                    email_source=email, extra={"skip_console_probe": True}
                )
            names = sorted(p.name for p in out.glob("typesafe-*.json"))
        self.assertTrue(a.ok and b.ok)
        self.assertEqual(len(names), 2)
        self.assertEqual(len(set(names)), 2)

    def test_pipeline_batch_continues_on_mail_miss(self):
        email = FakeEmail()
        provider = TypesafeProvider(proxy="")
        from providers.typesafe.protocol.flow import TypesafeRegisterError, TypesafeResult

        calls = {"n": 0}

        def _reg(**_kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TypesafeRegisterError("magic link timeout", kind="mail_miss")
            return TypesafeResult(
                ok=True,
                email="newuser@example.com",
                api_key="pipeline-this-run-key-xx",
                api_key_id="kid",
            )

        def _noop_preflight(extra, **_kw):
            base = dict(extra or {})
            base["_nodes_preflight_done"] = True
            base["_nodes_preflight"] = {"skipped": True, "reason": "test"}
            return base

        with tempfile.TemporaryDirectory() as td:
            with (
                patch(
                    "register_core.providers.typesafe_adapter.OUTPUT_DIR", Path(td)
                ),
                patch(
                    "providers.typesafe.protocol.flow.register_one",
                    side_effect=_reg,
                ),
                patch(
                    "register_core.util.proxy.preflight_nodes_for_register",
                    side_effect=_noop_preflight,
                ),
                patch(
                    "register_core.util.proxy.inject_attempt_proxy",
                    side_effect=lambda extra, **_kw: dict(extra or {}),
                ),
            ):
                pipe = Pipeline(
                    provider,
                    email_source=email,
                    verifier=TypesafeKeyVerifier(),
                    fail_fast=False,
                )
                stats = pipe.run(
                    2,
                    extra={
                        "egress": "direct",
                        "nodes_preflight": False,
                        "skip_console_probe": True,
                    },
                )
        self.assertEqual(stats.ok, 1)
        self.assertEqual(stats.fail, 1)
        self.assertEqual(stats.stopped_reason, "")
        self.assertEqual(calls["n"], 2)

    def test_pipeline_workers_cap_and_typesafe_only(self):
        ts = TypesafeProvider(proxy="")
        pipe = Pipeline(ts, fail_fast=True)
        self.assertEqual(pipe._worker_count({"threads": 8}, 100), 8)
        self.assertEqual(pipe._worker_count({"threads": 256}, 100), 32)
        self.assertEqual(pipe._worker_count({"threads": 8}, 3), 3)

        class Grokish:
            name = "grok"

        grok_pipe = Pipeline(Grokish(), fail_fast=True)  # type: ignore[arg-type]
        self.assertEqual(grok_pipe._worker_count({"threads": 8}, 100), 1)

    def test_pipeline_typesafe_parallel_runs_count(self):
        import threading

        email = FakeEmail()
        provider = TypesafeProvider(proxy="")
        from providers.typesafe.protocol.flow import TypesafeResult

        seen: list[int] = []
        lock = threading.Lock()

        def _reg(**_kw):
            with lock:
                seen.append(threading.get_ident())
            return TypesafeResult(
                ok=True,
                email="newuser@example.com",
                api_key="pipeline-this-run-key-xx",
                api_key_id="kid",
            )

        def _noop_preflight(extra, **_kw):
            base = dict(extra or {})
            base["_nodes_preflight_done"] = True
            base["_nodes_preflight"] = {"skipped": True, "reason": "test"}
            return base

        with tempfile.TemporaryDirectory() as td:
            with (
                patch(
                    "register_core.providers.typesafe_adapter.OUTPUT_DIR", Path(td)
                ),
                patch(
                    "providers.typesafe.protocol.flow.register_one",
                    side_effect=_reg,
                ),
                patch(
                    "register_core.util.proxy.preflight_nodes_for_register",
                    side_effect=_noop_preflight,
                ),
                patch(
                    "register_core.util.proxy.inject_attempt_proxy",
                    side_effect=lambda extra, **_kw: dict(extra or {}),
                ),
            ):
                pipe = Pipeline(
                    provider,
                    email_source=email,
                    verifier=TypesafeKeyVerifier(),
                    fail_fast=False,
                )
                stats = pipe.run(
                    4,
                    extra={
                        "egress": "direct",
                        "nodes_preflight": False,
                        "skip_console_probe": True,
                        "threads": 4,
                    },
                )
        self.assertEqual(stats.ok, 4)
        self.assertEqual(stats.fail, 0)
        self.assertEqual(len(seen), 4)
        self.assertEqual(email.allocated, 4)


if __name__ == "__main__":
    unittest.main()
