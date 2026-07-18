"""Grok / xAI provider — adapts existing register_cli + grok_register_ttk."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from register_core.contracts import RegisterResult
from register_core.email.base import EmailSource
from register_core.errors import FailFastError, ProviderError
from register_core.util.files import file_size, read_appended
from register_core.util.process import redact_log_tail, run_command

ROOT = Path(__file__).resolve().parents[2]
_SUCCESS_LOG = re.compile(r"\+\s*注册成功:\s*(\S+@\S+)")


class GrokProvider:
    name = "grok"

    def __init__(
        self,
        *,
        threads: int = 1,
        headless: bool | None = None,
        account_slot_retry: int = 0,
        accounts_file: str | None = None,
        extra_cli: list[str] | None = None,
        **_: Any,
    ) -> None:
        self.threads = max(1, int(threads))
        self.headless = headless
        self.account_slot_retry = account_slot_retry
        self.accounts_file = accounts_file or str(ROOT / "accounts_cli.txt")
        self.extra_cli = list(extra_cli or [])

    def register_one(
        self,
        *,
        email_source: EmailSource | None = None,
        extra: dict[str, Any] | None = None,
    ) -> RegisterResult:
        """Shell out to register_cli for one account.

        When email_source is set, allocate FIXED_EMAIL and set EMAIL_PROVIDER=fixed
        so ttk uses core mailbox; OTP goes through OTP_HELPER / REGISTER_OTP_SPEC_PATH.
        Success requires exit=0 **and** a this-run ledger increment (or
        success log email). secret_kind is sso only when SSO was captured.
        """
        extra = extra or {}
        py = sys.executable
        cli = ROOT / "register_cli.py"
        if not cli.is_file():
            raise FailFastError(f"register_cli.py missing at {cli}")

        accounts_file = str(extra.get("accounts_file") or self.accounts_file)
        off = file_size(accounts_file)

        cmd = [
            py,
            "-u",
            str(cli),
            "--extra",
            "1",
            "--threads",
            str(self.threads),
            "--account-slot-retry",
            str(self.account_slot_retry),
            "--accounts-file",
            accounts_file,
            "--fast",
        ]
        if self.headless is True:
            cmd.append("--headless")
        elif self.headless is False:
            cmd.append("--no-headless")
        cmd.extend(self.extra_cli)

        env = os.environ.copy()
        timeout_s = int(extra.get("timeout_s", 900) or 900)
        otp_timeout = float(extra.get("otp_timeout_s") or 180)
        # Forward pipeline attempt proxy into register_cli env (PROXY overlay).
        # Without this, inject_attempt_proxy is ignored by the shell-out path.
        proxy = str(extra.get("proxy") or "").strip()
        if proxy:
            env["PROXY"] = proxy
            # Keep CPA mint on same egress when child does inline mint.
            # Force-set (not setdefault): ambient CPA_PROXY from host must not win.
            env["CPA_PROXY"] = proxy
        mailbox = None
        mail_meta: dict[str, Any] = {}
        released = False
        success = False

        def _release() -> None:
            nonlocal released
            if released or mailbox is None or email_source is None:
                return
            released = True
            try:
                email_source.release(mailbox, success=success)
            except Exception:
                pass

        if email_source is not None:
            from register_core.util.mail_inject import prepare_mail_inject

            try:
                mailbox = prepare_mail_inject(
                    email_source,
                    env,
                    timeout_s=otp_timeout,
                    sender_hint="xai",
                    force_helper=True,
                    work_dir=ROOT / "logs" / "otp_bridge",
                )
            except Exception as exc:
                raise FailFastError(f"grok mail allocate failed: {exc}") from exc
            if mailbox is not None:
                mail_meta = {
                    "fixed_email": mailbox.address,
                    "email_source": getattr(email_source, "name", ""),
                    "otp_helper": bool(env.get("OTP_HELPER")),
                }

        try:
            try:
                proc = run_command(cmd, cwd=str(ROOT), env=env, timeout_s=timeout_s)
            except Exception as exc:
                raise FailFastError(f"grok register spawn failed: {exc}") from exc

            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            if proc.timed_out:
                raise ProviderError(f"grok register timeout after {timeout_s}s")

            # register_cli exit-code contract (authoritative, machine-readable):
            #   exit 2 = fatal (_fatal_stop set → SUMMARY_JSON "fatal":true
            #            + "fatal_reason":...). Hard stop, no re-burn / no empty spin.
            #   exit 1 = not product-usable (OTP timeout, verify fail, etc.) — NOT
            #            fatal; recoverable / retryable per strategy burn-cool.
            #   exit 0 = product ok.
            # Do NOT substring-match "fatal"/"alias"/"致命" against raw output: the
            # SUMMARY_JSON itself always contains the *keys* "fatal" and
            # "fatal_reason" (printed on every run, even when "fatal":false), so a
            # naive `k in lower(out)` would promote every exit-1 retryable failure
            # to FailFastError and stop the whole batch on a single transient OTP
            # timeout (observed in pxed smoke). Use the exit code as the contract;
            # keep the SUMMARY_JSON decode as a cross-check; fall back to a tight
            # marker only when stdout was empty (spawn path / no summary emitted).
            rc = int(proc.returncode)

            def _summary_fatal() -> tuple[bool, str]:
                """Decode register_cli SUMMARY_JSON if present; return (fatal, reason).

                register_cli prints one `SUMMARY_JSON {...}` line at exit; keep keys
                stable ("fatal" bool, "fatal_reason" str). We scan lines (not a
                full-output regex) so the last summary wins and a stray `{` earlier
                in logs can't fool the decoder.
                """
                last_summary = None
                for ln in out.splitlines():
                    s = ln.strip()
                    if s.startswith("SUMMARY_JSON") and s[len("SUMMARY_JSON"):].lstrip().startswith("{"):
                        last_summary = s[len("SUMMARY_JSON"):].lstrip()
                if last_summary:
                    try:
                        import json as _json

                        d = _json.loads(last_summary)
                        return bool(d.get("fatal")), str(d.get("fatal_reason") or "")
                    except Exception:
                        return False, ""
                return False, ""

            if rc != 0:
                sum_fatal, sum_reason = _summary_fatal()
                is_fatal = rc == 2 or sum_fatal
                # Tight fallback only when register_cli emitted no authority (e.g.
                # orphan-detected / ImportError before main()) so a genuine batch
                # stopper still surfaces. matched on whole-word-ish phrases only.
                if not is_fatal and not out.strip():
                    # no output at all from register_cli → treat as fatal spawn.
                    is_fatal = True
                if is_fatal:
                    # Surface the actual register_cli failure (boundary evidence):
                    # the subprocess output travels with the fatal signal, else prod
                    # smoke failures are undiagnosable. Redact secrets; keep tail.
                    reason = sum_reason or f"exit={rc}"
                    raise FailFastError(
                        f"grok fatal: {reason}\n"
                        f"--- register_cli output (redacted tail) ---\n"
                        f"{redact_log_tail(out, limit=4000)}"
                    )
                return RegisterResult(
                    ok=False,
                    provider=self.name,
                    email=(mailbox.address if mailbox else ""),
                    error=f"register_cli exit={rc}",
                    error_kind="provider",
                    secret_kind="none",
                    artifacts={
                        "exit_code": rc,
                        "summary_fatal": False,
                        "ledger": accounts_file,
                        "tail": redact_log_tail(out),
                        **mail_meta,
                    },
                )

            email, password, sso = self._parse_this_run(
                out=out,
                ledger_delta=read_appended(accounts_file, off),
            )
            if not email and mailbox is not None:
                email = mailbox.address
            if not email:
                return RegisterResult(
                    ok=False,
                    provider=self.name,
                    error="register_cli exit=0 but no this-run ledger/email",
                    error_kind="provider",
                    secret_kind="none",
                    artifacts={
                        "exit_code": 0,
                        "ledger": accounts_file,
                        "tail": redact_log_tail(out),
                        **mail_meta,
                    },
                )

            if not sso:
                # Email-only ledger row is incomplete for product success (no SSO / mint input).
                return RegisterResult(
                    ok=False,
                    provider=self.name,
                    email=email,
                    password=password,
                    secret="",
                    secret_kind="pending",
                    error="this-run email without SSO cookie (pending); not product-ready",
                    error_kind="provider",
                    artifacts={
                        "exit_code": 0,
                        "ledger": accounts_file,
                        "note": "require SSO in accounts ledger (email----pw----sso)",
                        "tail": redact_log_tail(out, limit=800),
                        **mail_meta,
                    },
                )

            success = True
            return RegisterResult(
                ok=True,
                provider=self.name,
                email=email,
                password=password,
                secret=sso,
                secret_kind="sso",
                artifacts={
                    "exit_code": 0,
                    "ledger": accounts_file,
                    "note": "sso captured; chat entitlement still via cpa_xai.probe",
                    "tail": redact_log_tail(out, limit=800),
                    **mail_meta,
                },
            )
        finally:
            _release()

    @staticmethod
    def _parse_this_run(*, out: str, ledger_delta: str) -> tuple[str, str, str]:
        email, password, sso = "", "", ""
        # Prefer ledger append (authoritative)
        for line in ledger_delta.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("----")
            if len(parts) >= 1 and "@" in parts[0]:
                email = parts[0].strip()
                password = parts[1].strip() if len(parts) > 1 else ""
                sso = parts[2].strip() if len(parts) > 2 else ""
        if email:
            return email, password, sso
        # Fallback: success log line
        m = _SUCCESS_LOG.search(out)
        if m:
            email = m.group(1).strip().rstrip(",;")
        return email, password, sso
