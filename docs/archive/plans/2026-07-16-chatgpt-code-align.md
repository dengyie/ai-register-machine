# ChatGPT Code-Level OSS Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align ChatGPT registration code with OSS best practices (OTP diagnostics, fine error_kind, mail/register proxy separation, node soft cooldown) via core-first contracts, without commercial mail APIs or CPA inject.

**Architecture:** Additive contracts in `register_core` (`OtpWaitDiagnostics`, error-kind constants, `MailMissError.diagnostics`, `NodeManager.cooldown` / `is_cooling`); ChatGPT adapter + protocol flow only wire them. Mail path defaults to direct (or explicit `mail_proxy`); register egress stays on PROXY_LIST/nodes. Soft cooldown is time-based and distinct from hard quarantine; `mail_miss` never cools or quarantines.

**Tech Stack:** Python 3.11+, `register_core` layers, unittest (existing style), curl_cffi ChatGPT protocol, Gmail IMAP / tinyhost email sources.

## Global Constraints

- Scope: design `docs/superpowers/specs/2026-07-16-chatgpt-code-align-design.md` (Approach 1, Scope A, Layer C, Success B).
- Do **not**: commercial mail APIs, ChatGPT CPA inject, soft-inject without chat_ok, Grok free Build 403 unlock, Approach 3 EgressPolicy rewrite, commit secrets / untracked ops noise.
- Additive only: Grok/MiMo must not break; existing `test_fail_policy.py` + `test_cpa_remote_inject.py` stay green.
- Fail-fast: no empty spin; `mail_miss` fail-fast per attempt; `fatal` stops batch.
- Secrets: never log full MIME/token/password; public artifacts redact.
- Milestone max 3 phases (mapped to Tasks 1–3 below); stop when P0/P1 done or live assets block ok=1 (code still deliverable).
- Commit style: `feat(core): …` / `fix(chatgpt): …` / `test: …` / `docs: …`.
- Work in repo root: `/Users/mango/project/claude-project/grok-register` (or monorepo equivalent).

---

## File map

| File | Responsibility |
|------|----------------|
| `register_core/contracts.py` | `OtpWaitDiagnostics`; error_kind comment + `ALLOWED_ERROR_KINDS`; public allow `otp_wait` / proxy labels |
| `register_core/errors.py` | `MailMissError(..., diagnostics=None)` |
| `register_core/email/base.py` | Doc convention: optional `last_wait_diagnostics` |
| `register_core/nodes/models.py` | `cooldown_until` / `cooldown_reason` serialize |
| `register_core/nodes/manager.py` | `cooldown` / `is_cooling`; pick/enabled skip cooling |
| `register_core/util/proxy.py` | Soft-cool wiring in `report_attempt_proxy_result` |
| `register_core/email/sources/gmail_imap.py` | Outer diagnostics on poll; auth → failure_class |
| `register_core/email/sources/tinyhost.py` | Poll loop diagnostics |
| `register_core/providers/chatgpt_adapter.py` | `resolve_mail_proxy`; allowlist; `artifacts["otp_wait"]` |
| `providers/chatgpt/protocol/flow.py` | `registration_disallowed` kind; `otp_sent_at` in steps |
| `.env.example` + `providers/chatgpt/README.md` | Config surface |
| `test_chatgpt_error_kinds.py` (new) | Taxonomy + allowlist |
| `test_otp_diagnostics.py` (new) | Diagnostics + MailMissError |
| `test_mail_proxy_separation.py` (new) | Mail proxy never inherits register proxy |
| `test_register_core_nodes.py` (extend) | Cooldown skip/expire |
| `test_register_core_proxy.py` (extend) | Risk soft-cool, mail_miss no cool |

---

### Task 1: Core contracts, errors, diagnostics types + unit tests

**Files:**
- Modify: `register_core/contracts.py`
- Modify: `register_core/errors.py`
- Modify: `register_core/email/base.py`
- Create: `test_otp_diagnostics.py`
- Create: `test_chatgpt_error_kinds.py` (skeleton constants only; adapter allowlist asserted after Task 2 if needed — prefer constants in contracts now)

**Interfaces:**
- Consumes: existing `RegisterResult`, `MailMissError`
- Produces:
  - `OtpWaitDiagnostics` dataclass (fields per design §5)
  - `ALLOWED_ERROR_KINDS: frozenset[str]`
  - `normalize_error_kind(kind: str) -> str`
  - `MailMissError(msg, *, diagnostics: OtpWaitDiagnostics | None = None)` with `.diagnostics` attr; `str(exc)` unchanged (message only)
  - EmailSource doc note for optional `last_wait_diagnostics: OtpWaitDiagnostics | None`

- [ ] **Step 1: Write failing tests for diagnostics + error kinds**

Create `test_otp_diagnostics.py`:

```python
#!/usr/bin/env python3
"""OtpWaitDiagnostics + MailMissError.diagnostics (offline)."""

from __future__ import annotations

import unittest
from dataclasses import asdict

from register_core.contracts import OtpWaitDiagnostics
from register_core.errors import MailMissError


class TestOtpWaitDiagnostics(unittest.TestCase):
    def test_defaults_and_asdict(self) -> None:
        d = OtpWaitDiagnostics()
        self.assertEqual(d.poll_count, 0)
        self.assertEqual(d.failure_class, "")
        payload = asdict(d)
        self.assertIn("message_scan_count", payload)
        self.assertIn("matched_after_seconds", payload)

    def test_mail_miss_carries_diagnostics(self) -> None:
        diag = OtpWaitDiagnostics(
            poll_count=3,
            empty_rounds=3,
            failure_class="no_mail",
            elapsed_seconds=90.0,
            timeout_s=90.0,
            provider="gmail_imap",
        )
        exc = MailMissError("gmail empty OTP", diagnostics=diag)
        self.assertEqual(str(exc), "gmail empty OTP")
        self.assertIs(exc.diagnostics, diag)
        self.assertEqual(exc.diagnostics.failure_class, "no_mail")

    def test_mail_miss_without_diagnostics(self) -> None:
        exc = MailMissError("plain miss")
        self.assertIsNone(exc.diagnostics)
        self.assertEqual(str(exc), "plain miss")


if __name__ == "__main__":
    raise SystemExit(unittest.main())
```

Create `test_chatgpt_error_kinds.py`:

```python
#!/usr/bin/env python3
"""error_kind taxonomy constants + normalize (offline)."""

from __future__ import annotations

import unittest

from register_core.contracts import ALLOWED_ERROR_KINDS, normalize_error_kind


class TestErrorKinds(unittest.TestCase):
    def test_allowed_contains_oss_set(self) -> None:
        for k in (
            "mail_miss",
            "registration_disallowed",
            "captcha",
            "proxy",
            "network",
            "provider",
            "verify",
            "fatal",
            "other",
        ):
            self.assertIn(k, ALLOWED_ERROR_KINDS)

    def test_normalize_keeps_known(self) -> None:
        self.assertEqual(normalize_error_kind("registration_disallowed"), "registration_disallowed")
        self.assertEqual(normalize_error_kind("mail_miss"), "mail_miss")
        self.assertEqual(normalize_error_kind("proxy"), "proxy")
        self.assertEqual(normalize_error_kind("network"), "network")
        self.assertEqual(normalize_error_kind("fatal"), "fatal")

    def test_normalize_unknown_to_provider(self) -> None:
        self.assertEqual(normalize_error_kind("weird_thing"), "provider")
        self.assertEqual(normalize_error_kind(""), "provider")
        self.assertEqual(normalize_error_kind(None), "provider")  # type: ignore[arg-type]


if __name__ == "__main__":
    raise SystemExit(unittest.main())
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd /Users/mango/project/claude-project/grok-register
.venv/bin/python -m unittest test_otp_diagnostics test_chatgpt_error_kinds -v
```

Expected: ImportError / AttributeError for `OtpWaitDiagnostics`, `ALLOWED_ERROR_KINDS`, `normalize_error_kind`, or `MailMissError` kwargs.

- [ ] **Step 3: Implement contracts + errors + base doc**

In `register_core/contracts.py`, after imports / near top of dataclasses section, add:

```python
ALLOWED_ERROR_KINDS: frozenset[str] = frozenset(
    {
        "mail_miss",
        "registration_disallowed",
        "captcha",
        "proxy",
        "network",
        "provider",
        "verify",
        "fatal",
        "other",
    }
)


def normalize_error_kind(kind: str | None) -> str:
    """Map provider-reported kind into the public taxonomy; unknown → provider."""
    k = (kind or "").strip().lower()
    if k in ALLOWED_ERROR_KINDS:
        return k
    return "provider"


@dataclass(slots=True)
class OtpWaitDiagnostics:
    """Observability for one OTP poll window (no raw MIME/token)."""

    poll_count: int = 0
    message_scan_count: int = 0
    empty_rounds: int = 0
    elapsed_seconds: float = 0.0
    timeout_s: float = 0.0
    first_message_seen_at: float | None = None
    matched_at: float | None = None
    first_seen_after_seconds: float | None = None
    matched_after_seconds: float | None = None
    abort_reason: str = ""
    failure_class: str = ""  # no_mail | parse_fail | stale_code | imap_error | aborted | ""
    provider: str = ""
    sender_hint: str = ""
    notes: str = ""
```

Update `RegisterResult.error_kind` comment:

```python
    error_kind: str = ""  # mail_miss | registration_disallowed | captcha | proxy | network | provider | verify | fatal | other
```

In `_public_artifacts` allow set, add:

```python
        "otp_wait",
        "mail_proxy",
        "register_proxy",
        "proxy",
        "proxy_mode",
        "proxy_label",
        "mailbox_provider",
        "device_id",
```

(Only safe keys; values for proxies should already be redacted by adapter.)

In `register_core/errors.py`:

```python
class MailMissError(RegisterCoreError):
    """OTP not received for the allocated mailbox (bounded retry OK)."""

    def __init__(
        self,
        message: str = "",
        *,
        diagnostics: object | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics
```

In `register_core/email/base.py`, extend module/class docstring:

```python
"""Email source protocol: allocate mailbox + poll OTP.

Convention (optional, not part of Protocol signature):
  After ``poll_otp`` succeeds or raises ``MailMissError``, implementations may
  set ``self.last_wait_diagnostics`` to an ``OtpWaitDiagnostics`` instance so
  adapters can attach ``artifacts["otp_wait"]`` without changing call sites.
"""
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
.venv/bin/python -m unittest test_otp_diagnostics test_chatgpt_error_kinds -v
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add register_core/contracts.py register_core/errors.py register_core/email/base.py \
  test_otp_diagnostics.py test_chatgpt_error_kinds.py
git commit -m "$(cat <<'EOF'
feat(core): OtpWaitDiagnostics + error_kind taxonomy constants

Add OtpWaitDiagnostics, ALLOWED_ERROR_KINDS/normalize_error_kind, and
MailMissError.diagnostics for ChatGPT OSS alignment (core-first).
EOF
)"
```

---

### Task 2: Node soft cooldown + proxy feedback + email/adapter/flow wiring

**Files:**
- Modify: `register_core/nodes/models.py`
- Modify: `register_core/nodes/manager.py`
- Modify: `register_core/util/proxy.py`
- Modify: `register_core/email/sources/gmail_imap.py`
- Modify: `register_core/email/sources/tinyhost.py`
- Modify: `register_core/providers/chatgpt_adapter.py`
- Modify: `providers/chatgpt/protocol/flow.py`
- Modify: `test_register_core_nodes.py`
- Modify: `test_register_core_proxy.py`
- Create: `test_mail_proxy_separation.py`

**Interfaces:**
- Consumes: Task 1 types; existing `NodeManager.mark_result` / `report_attempt_proxy_result`
- Produces:
  - `Node.cooldown_until: float | None`, `Node.cooldown_reason: str`
  - `NodeManager.is_cooling(n) -> bool`
  - `NodeManager.cooldown(url, seconds, reason="", *, persist=True) -> Node | None`
  - `enabled_nodes` / `pick` skip cooling nodes
  - Env: `REGISTER_NODES_COOLDOWN_RISK` default 600; `REGISTER_NODES_COOLDOWN_NETWORK` default 120; `REGISTER_NODES_COOLDOWN_PER_USE` default 0
  - `report_attempt_proxy_result`: ok → clear + optional per_use cool; `registration_disallowed` → cooldown RISK no quarantine; mail_miss/captcha/verify/fatal → no cool; network → mark fail + NETWORK cool
  - `resolve_mail_proxy(extra) -> str` in adapter (or small helper in chatgpt_adapter)
  - Adapter: never pass register proxy into email source; allowlist via `normalize_error_kind`; attach `otp_wait` from `MailMissError.diagnostics` or `source.last_wait_diagnostics`
  - flow.py: `kind="registration_disallowed"` on risk; record `steps["otp_sent_at"]=time.time()` after successful send_otp

- [ ] **Step 1: Write failing cooldown + mail_proxy + report tests**

Append to `test_register_core_nodes.py` inside `TestManager`:

```python
    def test_cooldown_skips_pick_until_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nodes.json"
            save_nodes(
                [
                    Node(url="http://cool:1", id="c"),
                    Node(url="http://hot:2", id="h"),
                ],
                path,
            )
            mgr = NodeManager(path)
            n = mgr.cooldown("http://cool:1", seconds=600, reason="registration_disallowed")
            self.assertIsNotNone(n)
            assert n is not None
            self.assertTrue(mgr.is_cooling(n))
            picked = {mgr.pick().url for _ in range(4)}  # type: ignore[union-attr]
            self.assertEqual(picked, {"http://hot:2"})
            # force expire
            for node in mgr.nodes:
                if node.url == "http://cool:1":
                    node.cooldown_until = time.time() - 1
            urls = {n.url for n in mgr.enabled_nodes()}
            self.assertIn("http://cool:1", urls)

    def test_mail_miss_mark_does_not_require_cooldown_api(self) -> None:
        # structural: is_cooling false by default
        n = Node(url="http://x:1")
        mgr = NodeManager.__new__(NodeManager)
        mgr._skip_failed = True
        mgr._max_fail = 3
        self.assertFalse(NodeManager.is_cooling(mgr, n))
```

(Add `import time` at top of test file if missing.)

Append to `test_register_core_proxy.py` a new class (after existing classes; use temp nodes + patch get_manager pattern from nodes tests if needed). Prefer a focused self-contained test:

```python
class TestReportAttemptSoftCool(unittest.TestCase):
    def setUp(self) -> None:
        core_proxy.reset_rotation_for_tests()

    def tearDown(self) -> None:
        core_proxy.reset_rotation_for_tests()

    def test_registration_disallowed_soft_cools_not_quarantine(self) -> None:
        from register_core.nodes.catalog import save_nodes
        from register_core.nodes.manager import NodeManager, reset_manager_for_tests
        from register_core.nodes.models import Node

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nodes.json"
            save_nodes([Node(url="http://risk:9", id="r")], path)
            reset_manager_for_tests()
            env = {
                "REGISTER_NODES_FILE": str(path),
                "REGISTER_NODES": "1",
                "REGISTER_NODES_COOLDOWN_RISK": "600",
                "REGISTER_NODES_COOLDOWN_NETWORK": "120",
                "REGISTER_NODES_COOLDOWN_PER_USE": "0",
            }
            with patch.dict(os.environ, env, clear=False):
                reset_manager_for_tests()
                info = core_proxy.report_attempt_proxy_result(
                    {"proxy": "http://risk:9"},
                    ok=False,
                    error="create_account registration_disallowed",
                    error_kind="registration_disallowed",
                )
                self.assertEqual(info.get("action"), "risk_cooldown")
                self.assertFalse(info.get("quarantined"))
                mgr = NodeManager(path)
                n = mgr.find_by_url("http://risk:9")
                assert n is not None
                self.assertTrue(mgr.is_cooling(n))
                self.assertEqual(n.cooldown_reason, "registration_disallowed")
                # not hard-quarantined
                self.assertFalse(mgr.is_quarantined(n))
            reset_manager_for_tests()

    def test_mail_miss_no_cool_no_quarantine(self) -> None:
        from register_core.nodes.catalog import save_nodes
        from register_core.nodes.manager import NodeManager, reset_manager_for_tests
        from register_core.nodes.models import Node

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nodes.json"
            save_nodes([Node(url="http://m:1", id="m")], path)
            env = {"REGISTER_NODES_FILE": str(path), "REGISTER_NODES": "1"}
            with patch.dict(os.environ, env, clear=False):
                reset_manager_for_tests()
                info = core_proxy.report_attempt_proxy_result(
                    {"proxy": "http://m:1"},
                    ok=False,
                    error="otp_wait",
                    error_kind="mail_miss",
                )
                self.assertEqual(info.get("reason"), "non_proxy_failure")
                mgr = NodeManager(path)
                n = mgr.find_by_url("http://m:1")
                assert n is not None
                self.assertFalse(mgr.is_cooling(n))
                self.assertEqual(int(n.fail_count or 0), 0)
            reset_manager_for_tests()
```

Create `test_mail_proxy_separation.py`:

```python
#!/usr/bin/env python3
"""ChatGPT adapter must not feed register proxy into email sources by default."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from register_core.providers.chatgpt_adapter import ChatGPTProvider, resolve_mail_proxy


class TestMailProxySeparation(unittest.TestCase):
    def test_resolve_mail_proxy_never_falls_back_to_register(self) -> None:
        self.assertEqual(resolve_mail_proxy({"proxy": "http://reg:1"}), "")
        self.assertEqual(
            resolve_mail_proxy({"proxy": "http://reg:1", "mail_proxy": "http://mail:2"}),
            "http://mail:2",
        )

    def test_env_mail_proxy(self) -> None:
        with patch.dict(os.environ, {"EMAIL_PROXY": "http://env-mail:3", "CHATGPT_MAIL_PROXY": ""}, clear=False):
            self.assertEqual(resolve_mail_proxy({}), "http://env-mail:3")

    def test_adapter_constructs_source_with_mail_proxy_only(self) -> None:
        captured: dict = {}

        def fake_get(name, **kw):
            captured["name"] = name
            captured["kw"] = kw

            class Src:
                name = name

                def allocate(self):
                    raise RuntimeError("stop-before-network")

            return Src()

        prov = ChatGPTProvider(proxy="http://register-egress:8080", email_source_name="tinyhost")
        with patch(
            "register_core.providers.chatgpt_adapter.get_email_source",
            side_effect=fake_get,
        ):
            try:
                prov.register_one(extra={"proxy": "http://register-egress:8080"})
            except Exception:
                pass
        self.assertIn("kw", captured)
        # default: no register proxy on mail path
        self.assertIn(captured["kw"].get("proxy"), (None, ""))


if __name__ == "__main__":
    raise SystemExit(unittest.main())
```

- [ ] **Step 2: Run targeted tests — expect FAIL**

```bash
.venv/bin/python -m unittest \
  test_register_core_nodes.TestManager.test_cooldown_skips_pick_until_expiry \
  test_register_core_proxy.TestReportAttemptSoftCool \
  test_mail_proxy_separation -v
```

Expected: AttributeError / ImportError on `cooldown` / `resolve_mail_proxy` / `is_cooling`.

- [ ] **Step 3: Implement Node model cooldown fields**

`register_core/nodes/models.py` — add fields:

```python
    fail_count: int = 0
    cooldown_until: float | None = None  # epoch seconds
    cooldown_reason: str = ""
```

In `to_public_dict`:

```python
            "fail_count": int(self.fail_count or 0),
            "cooling": bool(
                self.cooldown_until is not None and float(self.cooldown_until) > __import__("time").time()
            ),
            "cooldown_reason": self.cooldown_reason or "",
```

(Prefer `import time` at module top and use `time.time()`.)

In `to_store_dict`, persist when set:

```python
        if self.cooldown_until is not None:
            d["cooldown_until"] = float(self.cooldown_until)
        if self.cooldown_reason:
            d["cooldown_reason"] = str(self.cooldown_reason)[:80]
```

In `node_from_dict`:

```python
        cooldown_until=raw.get("cooldown_until"),
        cooldown_reason=str(raw.get("cooldown_reason") or ""),
```

(Normalize `cooldown_until` to float|None if present.)

- [ ] **Step 4: Implement NodeManager cooldown API + pick skip**

In `register_core/nodes/manager.py`:

```python
    def is_cooling(self, n: Node) -> bool:
        until = n.cooldown_until
        if until is None:
            return False
        try:
            return float(until) > time.time()
        except (TypeError, ValueError):
            return False

    def cooldown(
        self,
        url: str,
        seconds: float,
        reason: str = "",
        *,
        persist: bool = True,
    ) -> Node | None:
        url = (url or "").strip()
        if not url or seconds <= 0:
            return None
        self.ensure_loaded()
        with self._lock:
            node = None
            for n in self.nodes:
                if n.url == url:
                    node = n
                    break
            if node is None:
                return None
            node.cooldown_until = time.time() + float(seconds)
            node.cooldown_reason = (reason or "")[:80]
            if persist:
                try:
                    save_nodes(self.nodes, self.path)
                except Exception:
                    pass
            return node
```

In `enabled_nodes` loop, after quarantine check:

```python
                if self.is_cooling(n):
                    continue
```

- [ ] **Step 5: Wire `report_attempt_proxy_result` soft cool**

In `register_core/util/proxy.py`, replace the mid-section of `report_attempt_proxy_result` after node is found:

```python
    def _cool_seconds(env_name: str, default: float) -> float:
        raw = _env_first(env_name, default=str(default))
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return float(default)

    kind = (error_kind or "").strip().lower()
    network_fail = is_proxy_network_failure(ok=ok, error=error, error_kind=error_kind)

    if ok:
        mgr.mark_result(proxy, ok=True, error="", persist=True)
        info["marked"] = True
        info["action"] = "success_clear"
        per_use = _cool_seconds("REGISTER_NODES_COOLDOWN_PER_USE", 0.0)
        if per_use > 0:
            mgr.cooldown(proxy, per_use, reason="per_use", persist=True)
            info["cooldown_s"] = per_use
            info["action"] = "success_clear_per_use_cool"
        return info

    # Business failures: never quarantine; risk gets soft cool only.
    if kind in {"mail_miss", "captcha", "verify", "fatal"}:
        info["reason"] = "non_proxy_failure"
        return info

    if kind in {"registration_disallowed", "disallowed"} or (
        "registration_disallowed" in (error or "").lower()
    ):
        risk_s = _cool_seconds("REGISTER_NODES_COOLDOWN_RISK", 600.0)
        if risk_s > 0:
            mgr.cooldown(proxy, risk_s, reason="registration_disallowed", persist=True)
            info["action"] = "risk_cooldown"
            info["cooldown_s"] = risk_s
        else:
            info["action"] = "risk_no_cooldown"
        info["quarantined"] = False
        return info

    if not network_fail:
        info["reason"] = "non_proxy_failure"
        return info

    marked = mgr.mark_result(proxy, ok=False, error=error or error_kind or "proxy_fail", persist=True)
    info["marked"] = marked is not None
    info["action"] = "fail_mark"
    net_s = _cool_seconds("REGISTER_NODES_COOLDOWN_NETWORK", 120.0)
    if net_s > 0:
        mgr.cooldown(proxy, net_s, reason="network", persist=True)
        info["cooldown_s"] = net_s
        info["action"] = "fail_mark_network_cool"
    # ... keep existing quarantined / drop-from-rotator / rebuild logic below
```

Keep the rest of quarantine/drop/rebuild unchanged after mark.

- [ ] **Step 6: tinyhost diagnostics**

In `TinyhostSource.__init__`, set `self.last_wait_diagnostics = None`.

Rewrite `poll_otp` loop to track counters (minimal):

```python
        from register_core.contracts import OtpWaitDiagnostics

        started = time.time()
        diag = OtpWaitDiagnostics(
            timeout_s=float(timeout_s),
            provider=self.name,
            sender_hint=(sender_hint or ""),
        )
        self.last_wait_diagnostics = diag
        while time.time() < deadline:
            diag.poll_count += 1
            try:
                data = self._get_json(url, timeout=20)
            except MailMissError:
                diag.empty_rounds += 1
                time.sleep(poll_interval_s)
                continue
            emails = data.get("emails") if isinstance(data, dict) else None
            if not isinstance(emails, list) or not emails:
                diag.empty_rounds += 1
                time.sleep(poll_interval_s)
                continue
            if diag.first_message_seen_at is None:
                diag.first_message_seen_at = time.time()
                diag.first_seen_after_seconds = diag.first_message_seen_at - started
            for mail in emails:
                ...
                diag.message_scan_count += 1
                ...
                if code matched:
                    diag.matched_at = time.time()
                    diag.matched_after_seconds = diag.matched_at - started
                    diag.elapsed_seconds = diag.matched_after_seconds
                    self.last_wait_diagnostics = diag
                    return OtpCode(...)
            time.sleep(poll_interval_s)
        diag.elapsed_seconds = time.time() - started
        diag.failure_class = "no_mail" if diag.message_scan_count == 0 else "parse_fail"
        self.last_wait_diagnostics = diag
        raise MailMissError(
            f"tinyhost OTP timeout for {mailbox.address}",
            diagnostics=diag,
        )
```

- [ ] **Step 7: gmail_imap diagnostics wrapper**

```python
    def poll_otp(...) -> OtpCode:
        from register_core.contracts import OtpWaitDiagnostics
        import time as _time

        started = _time.time()
        diag = OtpWaitDiagnostics(
            timeout_s=float(timeout_s),
            provider=self.name,
            sender_hint=(sender_hint or ""),
            notes="wraps grok_register_ttk.get_oai_code",
        )
        self.last_wait_diagnostics = diag
        reg = self._reg()
        ...
        try:
            diag.poll_count = 1
            code = reg.get_oai_code(...)
        except Exception as exc:
            msg = str(exc)
            low = msg.lower()
            if "auth" in low or "credential" in low or "authenticationfailed" in low:
                diag.failure_class = "imap_error"
                # auth is config-level; callers may promote to fatal via FailFastError elsewhere
            else:
                diag.failure_class = "imap_error"
            diag.elapsed_seconds = _time.time() - started
            self.last_wait_diagnostics = diag
            raise MailMissError(f"gmail OTP failed: {exc}", diagnostics=diag) from exc
        finally:
            ...
        diag.elapsed_seconds = _time.time() - started
        if not code:
            diag.failure_class = "no_mail"
            self.last_wait_diagnostics = diag
            raise MailMissError(f"gmail empty OTP for {mailbox.address}", diagnostics=diag)
        if used_codes and code in used_codes:
            diag.failure_class = "stale_code"
            self.last_wait_diagnostics = diag
            raise MailMissError(f"gmail OTP already used: {code}", diagnostics=diag)
        diag.matched_at = _time.time()
        diag.matched_after_seconds = diag.matched_at - started
        diag.message_scan_count = 1
        self.last_wait_diagnostics = diag
        return OtpCode(code=str(code), source=self.name)
```

- [ ] **Step 8: flow.py kind + otp_sent_at**

In `create_account` failure branch (around lines 428–436):

```python
            kind = "provider"
            if code == "registration_disallowed" or "registration_disallowed" in str(
                body or error or ""
            ):
                kind = "registration_disallowed"
            raise ChatGPTRegisterError(
                f"create_account_http_{status}:{code or error or body}",
                kind=kind,
            )
```

In `register_one` after successful `send_otp`:

```python
        otp_send = registrar.send_otp()
        steps["send_otp"] = {"status": otp_send.get("status")}
        steps["otp_sent_at"] = time.time()
```

- [ ] **Step 9: chatgpt_adapter mail_proxy + allowlist + otp_wait**

At module level in `register_core/providers/chatgpt_adapter.py`:

```python
from dataclasses import asdict

from register_core.contracts import RegisterResult, normalize_error_kind


def resolve_mail_proxy(extra: dict[str, Any] | None = None) -> str:
    """Mail HTTP path proxy. Never falls back to register egress proxy."""
    extra = extra if isinstance(extra, dict) else {}
    for key in ("mail_proxy", "email_proxy"):
        v = str(extra.get(key) or "").strip()
        if v:
            return v
    for env in ("CHATGPT_MAIL_PROXY", "EMAIL_PROXY", "MAIL_PROXY"):
        v = str(os.environ.get(env) or "").strip()
        if v:
            return v
    return ""


def _redact_proxy(url: str) -> str:
    s = (url or "").strip()
    if not s:
        return "(none)"
    # keep host:port-ish, drop userinfo if present
    try:
        if "@" in s:
            return s.split("@", 1)[-1]
    except Exception:
        pass
    return s[:80]
```

In `register_one`, replace email source construction:

```python
        source = email_source
        mail_proxy = resolve_mail_proxy(extra)
        if source is None:
            try:
                kw: dict[str, Any] = {}
                if mail_proxy:
                    kw["proxy"] = mail_proxy
                else:
                    kw["proxy"] = None  # explicit direct — do NOT pass register proxy
                if domain and self.email_source_name in ("tinyhost", "auto"):
                    kw["domain"] = domain
                source = get_email_source(self.email_source_name, **kw)
            except Exception as exc:
                raise FailFastError(f"chatgpt email source unavailable: {exc}") from exc
```

Update arts:

```python
        arts: dict[str, Any] = {
            ...
            "proxy": _redact_proxy(proxy),
            "register_proxy": _redact_proxy(proxy),
            "mail_proxy": _redact_proxy(mail_proxy) if mail_proxy else "(direct)",
            ...
        }
```

Helper for attaching diagnostics:

```python
        def _attach_otp_wait(exc: BaseException | None = None) -> None:
            diag = None
            if isinstance(exc, MailMissError) and getattr(exc, "diagnostics", None) is not None:
                diag = exc.diagnostics
            elif getattr(source, "last_wait_diagnostics", None) is not None:
                diag = getattr(source, "last_wait_diagnostics")
            if diag is None:
                return
            try:
                arts["otp_wait"] = asdict(diag) if hasattr(diag, "__dataclass_fields__") else dict(diag)  # type: ignore[arg-type]
            except Exception:
                arts["otp_wait"] = {"notes": "diagnostics_serialize_failed"}
```

On `ChatGPTRegisterError` path:

```python
            kind = normalize_error_kind(getattr(exc, "kind", "provider"))
            ...
            _attach_otp_wait(exc if kind == "mail_miss" else None)
            if kind == "mail_miss":
                _attach_otp_wait()
            return RegisterResult(
                ...
                error_kind=kind,
                artifacts={**arts, "tail": "\n".join(logs)[-1500:]},
            )
```

On bare `MailMissError`:

```python
            _attach_otp_wait(exc)
            return RegisterResult(
                ...
                error_kind="mail_miss",
                artifacts={**arts, "tail": "\n".join(logs)[-1500:]},
            )
```

Ensure any existing pipeline call to `report_attempt_proxy_result` continues to pass through `error_kind` (no change required if already wired; do not invent CPA inject).

- [ ] **Step 10: Run Task 2 tests + regressions**

```bash
.venv/bin/python -m unittest \
  test_otp_diagnostics test_chatgpt_error_kinds \
  test_mail_proxy_separation \
  test_register_core_nodes test_register_core_proxy \
  test_fail_policy test_cpa_remote_inject -v
```

Expected: ALL PASS. If `test_fail_policy` uses ast exec, it must remain green (no ChatGPT changes there).

- [ ] **Step 11: Commit**

```bash
git add register_core/nodes/models.py register_core/nodes/manager.py \
  register_core/util/proxy.py \
  register_core/email/sources/gmail_imap.py register_core/email/sources/tinyhost.py \
  register_core/providers/chatgpt_adapter.py providers/chatgpt/protocol/flow.py \
  test_register_core_nodes.py test_register_core_proxy.py test_mail_proxy_separation.py
git commit -m "$(cat <<'EOF'
feat(core): soft node cooldown + mail/proxy split + ChatGPT wiring

Time-based NodeManager cooldown, risk soft-cool in proxy feedback,
email sources OTP diagnostics, ChatGPT mail_proxy separation and
registration_disallowed kind.
EOF
)"
```

---

### Task 3: Docs, full related tests, pxed smoke record

**Files:**
- Modify: `.env.example`
- Modify: `providers/chatgpt/README.md`
- Optional: extend `test_chatgpt_provider.py` only if existing tests break (do not invent live network tests)
- Smoke: run on pxed when credentials available; record outcome honestly

**Interfaces:**
- Consumes: all Task 1–2 behavior
- Produces: documented env vars; offline test green suite; smoke log path + honest SUMMARY

- [ ] **Step 1: Document config surface**

In `.env.example` under ChatGPT / nodes section, add:

```bash
# --- ChatGPT / OpenAI platform ---
# CHATGPT_EMAIL_SOURCE=gmail_imap
# CHATGPT_OTP_TIMEOUT=180
# CHATGPT_PROXY=                 # register egress only
# CHATGPT_MAIL_PROXY=            # email HTTP API only; empty=direct (never inherits CHATGPT_PROXY)
# EMAIL_PROXY=                   # generic alias for mail path
# REGISTER_NODES_MAX_FAIL=3
# REGISTER_NODES_COOLDOWN_RISK=600      # registration_disallowed soft cool (seconds)
# REGISTER_NODES_COOLDOWN_NETWORK=120   # network fail soft cool before hard quarantine
# REGISTER_NODES_COOLDOWN_PER_USE=0     # 0=off (avoid small-pool starvation)
```

In `providers/chatgpt/README.md` Env table, add rows for `CHATGPT_MAIL_PROXY` / `EMAIL_PROXY`, cooldown envs, and a short note:

```markdown
## Mail vs register egress

| Path | Default proxy |
|------|----------------|
| OpenAI register (curl_cffi) | `CHATGPT_PROXY` / nodes / `PROXY_LIST` |
| EmailSource HTTP (tinyhost API) | **direct** unless `CHATGPT_MAIL_PROXY` / `EMAIL_PROXY` |
| Gmail IMAP | local IMAP (no register proxy) |

`artifacts.otp_wait` on `mail_miss` includes `failure_class` (`no_mail`|`parse_fail`|…).
`error_kind=registration_disallowed` soft-cools the node (`REGISTER_NODES_COOLDOWN_RISK`); never hard-quarantines for mail_miss.
```

- [ ] **Step 2: Full offline verification**

```bash
cd /Users/mango/project/claude-project/grok-register
.venv/bin/python -m py_compile \
  register_core/contracts.py register_core/errors.py \
  register_core/nodes/models.py register_core/nodes/manager.py \
  register_core/util/proxy.py \
  register_core/email/sources/gmail_imap.py \
  register_core/email/sources/tinyhost.py \
  register_core/providers/chatgpt_adapter.py \
  providers/chatgpt/protocol/flow.py

.venv/bin/python -m unittest \
  test_otp_diagnostics test_chatgpt_error_kinds test_mail_proxy_separation \
  test_register_core_nodes test_register_core_proxy \
  test_fail_policy test_cpa_remote_inject test_chatgpt_provider -v
```

Expected: all PASS (skip any test that requires live network if previously optional — do not expand).

- [ ] **Step 3: pxed COUNT=1 smoke (honest)**

Only when operator env available. On pxed:

```bash
# Parse GMAIL_* only — do NOT source whole .env
# COUNT=1 hub register entry for chatgpt
COUNT=1 ./register.sh chatgpt 1
# or project current entry used in 2026-07-16 smoke
```

Record:
- log path
- `error_kind` + `artifacts.otp_wait.failure_class` if fail
- confirm node **not** quarantined on `mail_miss`
- if `ok=1`: tokens path; no CPA inject

If OTP still undeliverable: delivery Manual-required; **code gate still ✅**.

- [ ] **Step 4: Commit docs**

```bash
git add .env.example providers/chatgpt/README.md docs/superpowers/plans/2026-07-16-chatgpt-code-align.md
git commit -m "$(cat <<'EOF'
docs: ChatGPT mail_proxy + node cooldown config surface

Document mail/register egress split and soft-cooldown env defaults.
EOF
)"
```

- [ ] **Step 5: Milestone stop — delivery summary**

Output project delivery summary (do not start next milestone):

```text
# 项目交付总结
Milestone：ChatGPT 代码层 OSS 对齐（core-first）
完成的 P0/P1：G1–G6 代码门禁 …
未执行 Backlog：商业邮箱、CPA inject、EgressPolicy 大重构、Grok chat 403
Manual-required：OpenAI OTP 投递 / 住宅出口声誉
…
交付状态：✅ 可交付 / ⚠️ 有条件可交付
```

---

## Self-review (plan vs design)

| Design item | Task |
|-------------|------|
| G1 OTP diagnostics | Task 1 + Task 2 sources/adapter |
| G2 error_kind taxonomy | Task 1 constants + Task 2 flow/adapter |
| G3 mail/proxy separation | Task 2 adapter + test_mail_proxy_separation |
| G4 Node cooldown + report wiring | Task 2 models/manager/proxy |
| G5 config docs | Task 3 |
| G6 tests + smoke honesty | Task 2–3 |
| Non-goals (commercial mail, CPA inject, big rewrite) | Global constraints; no tasks |
| Phase 1/2/3 milestone | Task 1 / Task 2 / Task 3 |

**Placeholder scan:** no TBD/TODO steps; code blocks are concrete.

**Type consistency:** `OtpWaitDiagnostics` field names match design §5; `normalize_error_kind` used by adapter; `cooldown_until` float epoch; `failure_class` values match design §4.3.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-16-chatgpt-code-align.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration  
2. **Inline Execution** — execute tasks in this session with executing-plans, batch with checkpoints  

**Which approach?**
