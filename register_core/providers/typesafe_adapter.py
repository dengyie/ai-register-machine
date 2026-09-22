"""typesafe.ai / jev console provider — in-process EmailSource consumer."""

from __future__ import annotations

import os
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from register_core.contracts import RegisterResult, normalize_error_kind
from register_core.email.base import EmailSource
from register_core.email.mail_proxy import resolve_mail_proxy
from register_core.email.registry import get_email_source
from register_core.errors import FailFastError, MailMissError, ProviderError

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROTOCOL_DIR = ROOT / "providers" / "typesafe"
OUTPUT_DIR = PROTOCOL_DIR / "output"

__all__ = ["TypesafeProvider", "resolve_mail_proxy"]


def _redact_proxy(url: str) -> str:
    s = (url or "").strip()
    if not s:
        return "(none)"
    try:
        if "@" in s:
            return s.split("@", 1)[-1]
    except Exception:
        pass
    return s[:80]


class TypesafeProvider:
    """In-process typesafe.ai Stytch magic-link register.

    Consumes EmailSource (default tinyhost). Produces an API key as the
    primary secret. Never injects production CPA. Count/concurrency follow
    Pipeline — do not use the original 512/256 farm defaults.
    """

    name = "typesafe"

    def __init__(
        self,
        *,
        proxy: str | None = None,
        email_source_name: str | None = None,
        otp_timeout_s: float = 180,
        email_domain: str | None = None,
        api_key_name: str | None = None,
        **_: Any,
    ) -> None:
        self.proxy = (
            proxy
            or os.environ.get("TYPESAFE_PROXY")
            or os.environ.get("CHATGPT_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTPS_PROXY")
            or ""
        )
        # tinyhost returns full message bodies so the Stytch URL can be parsed.
        # Cloudflare/gmail OTP wrappers only extract 6-digit codes.
        self.email_source_name = (
            email_source_name
            or os.environ.get("TYPESAFE_EMAIL_SOURCE")
            or "tinyhost"
        )
        self.otp_timeout_s = float(
            os.environ.get("TYPESAFE_OTP_TIMEOUT")
            or os.environ.get("TYPESAFE_MAIL_TIMEOUT")
            or otp_timeout_s
        )
        raw_domain = (
            email_domain
            if email_domain is not None
            else os.environ.get("TYPESAFE_EMAIL_DOMAIN", "")
        )
        self.email_domain = str(raw_domain or "").strip() or None
        self.api_key_name = (
            api_key_name
            or os.environ.get("TYPESAFE_API_KEY_NAME")
            or "register-core"
        )

    def register_one(
        self,
        *,
        email_source: EmailSource | None = None,
        extra: dict[str, Any] | None = None,
    ) -> RegisterResult:
        from providers.typesafe.protocol.flow import (
            TypesafeRegisterError,
            probe_console,
            register_one,
            save_result,
        )
        from providers.typesafe.protocol.session import create_session

        extra = dict(extra or {})
        if not str(extra.get("proxy") or "").strip():
            try:
                from register_core.util.proxy import resolve_attempt_proxy

                resolved, rot_info = resolve_attempt_proxy(extra)
                if resolved:
                    extra["proxy"] = resolved
                if rot_info:
                    extra.setdefault("_proxy_rotate", rot_info)
            except Exception:
                pass
        proxy = str(extra.get("proxy") or self.proxy or "").strip()
        mail_proxy = resolve_mail_proxy(extra)
        otp_timeout = float(extra.get("otp_timeout_s") or self.otp_timeout_s)
        domain = str(extra.get("email_domain") or self.email_domain or "").strip() or None
        api_key_name = str(extra.get("api_key_name") or self.api_key_name or "").strip()

        source = email_source
        if source is None:
            try:
                kw: dict[str, Any] = {}
                if mail_proxy:
                    kw["proxy"] = mail_proxy
                else:
                    kw["proxy"] = None
                if domain and self.email_source_name in ("tinyhost", "auto"):
                    kw["domain"] = domain
                source = get_email_source(self.email_source_name, **kw)
            except Exception as exc:
                raise FailFastError(f"typesafe email source unavailable: {exc}") from exc
        else:
            if mail_proxy and hasattr(source, "proxy"):
                cur = str(getattr(source, "proxy", "") or "").strip()
                if not cur:
                    try:
                        source.proxy = mail_proxy  # type: ignore[attr-defined]
                    except Exception:
                        pass
            if domain and getattr(source, "name", "") == "tinyhost":
                try:
                    source.forced_domain = domain  # type: ignore[attr-defined]
                except Exception:
                    pass

        logs: list[str] = []

        def log(msg: str) -> None:
            line = str(msg)
            logs.append(line)
            print(f"[typesafe] {line}", flush=True)

        rot_meta = extra.get("_proxy_rotate") if isinstance(extra.get("_proxy_rotate"), dict) else {}
        arts: dict[str, Any] = {
            "runtime": str(PROTOCOL_DIR),
            "email_source": getattr(source, "name", self.email_source_name),
            "proxy": _redact_proxy(proxy),
            "register_proxy": _redact_proxy(proxy),
            "mail_proxy": _redact_proxy(mail_proxy) if mail_proxy else "(direct)",
            "proxy_mode": str(rot_meta.get("mode") or "fixed"),
            "proxy_label": str(rot_meta.get("label") or proxy or "(none)"),
            "note": "in-process typesafe.ai stytch magic-link; no cpa inject",
            "protocol": "typesafe_stytch_magic_link",
        }

        skip_probe = str(extra.get("skip_console_probe") or os.environ.get("TYPESAFE_SKIP_CONSOLE_PROBE") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        sess = extra.get("_session")
        if sess is None:
            sess = create_session(proxy)
        probe_action = extra.get("_login_action") if isinstance(extra.get("_login_action"), dict) else None
        if not skip_probe:
            try:
                probe = probe_console(session=sess, proxy=proxy, log=log)
                if isinstance(probe.get("action"), dict):
                    probe_action = probe["action"]
                arts["console_probe"] = {
                    "ok": True,
                    "status": probe.get("status"),
                    "index": probe.get("index"),
                    "deployment_id": probe.get("deployment_id"),
                }
            except TypesafeRegisterError as exc:
                kind = normalize_error_kind(getattr(exc, "kind", "network"))
                arts["fail_step"] = str(getattr(exc, "step", "") or "console_probe")
                arts["console_probe"] = {"ok": False, "error": str(exc)[:200]}
                if isinstance(exc.steps, dict) and exc.steps:
                    arts["steps"] = exc.steps
                    arts["step_keys"] = sorted(str(k) for k in exc.steps.keys())
                if kind == "fatal":
                    raise FailFastError(str(exc)) from exc
                return RegisterResult(
                    ok=False,
                    provider=self.name,
                    error=str(exc),
                    error_kind=kind,
                    secret_kind="none",
                    artifacts={**arts, "tail": "\n".join(logs)[-1500:]},
                )

        mailbox = source.allocate()
        email = (mailbox.address or "").strip()
        if not email or "@" not in email:
            raise FailFastError("typesafe allocate returned empty mailbox")

        t0 = time.time()
        used_codes: set[str] = set()

        def magic_link_provider() -> str:
            otp = source.poll_otp(
                mailbox,
                timeout_s=otp_timeout,
                poll_interval_s=2.5,
                used_codes=used_codes or None,
                newer_than_epoch=t0 - 10,
                sender_hint="typesafe",
            )
            code = str(getattr(otp, "code", "") or "").strip()
            if code:
                used_codes.add(code)
            return code

        arts["mailbox_provider"] = mailbox.provider
        arts["email_source"] = getattr(source, "name", self.email_source_name)

        def _attach_otp_wait(exc: BaseException | None = None) -> None:
            # Pipeline shares one EmailSource across workers. last_wait_diagnostics
            # is one slot, so a neighbor's poll overwrites it (n=100: two
            # mail_miss rows carried the same 19.48s diag). Only the exception
            # raised by this wait is this attempt's.
            diag = None
            cur: BaseException | None = exc
            while cur is not None and diag is None:
                if isinstance(cur, MailMissError) and getattr(cur, "diagnostics", None) is not None:
                    diag = cur.diagnostics
                cur = cur.__cause__ or cur.__context__
            if diag is None:
                arts["otp_wait"] = {"notes": "diagnostics_unavailable"}
                return
            try:
                arts["otp_wait"] = (
                    asdict(diag) if hasattr(diag, "__dataclass_fields__") else dict(diag)  # type: ignore[arg-type]
                )
            except Exception:
                arts["otp_wait"] = {"notes": "diagnostics_serialize_failed"}

        try:
            result = register_one(
                email=email,
                proxy=proxy,
                magic_link_provider=magic_link_provider,
                log=log,
                api_key_name=api_key_name,
                session=sess,
                action=probe_action,
            )
        except TypesafeRegisterError as exc:
            kind = normalize_error_kind(getattr(exc, "kind", "provider"))
            fail_step = str(getattr(exc, "step", "") or "").strip()
            partial_steps = getattr(exc, "steps", None)
            if isinstance(partial_steps, dict) and partial_steps:
                arts["steps"] = partial_steps
                arts["step_keys"] = sorted(str(k) for k in partial_steps.keys())
            if fail_step:
                arts["fail_step"] = fail_step
            try:
                source.release(mailbox, success=False)
            except Exception:
                pass
            if kind == "fatal":
                raise FailFastError(str(exc)) from exc
            if kind == "mail_miss":
                _attach_otp_wait(exc)
            return RegisterResult(
                ok=False,
                provider=self.name,
                email=email,
                error=str(exc),
                error_kind=kind,
                secret_kind="none",
                artifacts={**arts, "tail": "\n".join(logs)[-1500:]},
            )
        except MailMissError as exc:
            try:
                source.release(mailbox, success=False)
            except Exception:
                pass
            _attach_otp_wait(exc)
            return RegisterResult(
                ok=False,
                provider=self.name,
                email=email,
                error=str(exc),
                error_kind="mail_miss",
                secret_kind="none",
                artifacts={**arts, "tail": "\n".join(logs)[-1500:]},
            )
        except Exception as exc:
            try:
                source.release(mailbox, success=False)
            except Exception:
                pass
            raise ProviderError(f"typesafe unexpected: {exc}") from exc

        arts["tail"] = "\n".join(logs)[-1500:]
        if isinstance(result.steps, dict) and result.steps:
            arts["steps"] = result.steps
            arts["step_keys"] = sorted(str(k) for k in result.steps.keys())
        else:
            arts["steps"] = {}
            arts["step_keys"] = []
        fail_step = str(getattr(result, "fail_step", "") or "").strip()
        if fail_step:
            arts["fail_step"] = fail_step
        arts["deployment_id"] = result.deployment_id
        arts["deployment_mismatch"] = bool(result.deployment_mismatch)
        arts["user_id"] = result.user_id
        arts["organization_id"] = result.organization_id

        if not result.ok:
            try:
                source.release(mailbox, success=False)
            except Exception:
                pass
            return RegisterResult(
                ok=False,
                provider=self.name,
                email=result.email or email,
                error=result.error or "register_failed",
                error_kind=normalize_error_kind(result.error_kind or "provider"),
                secret_kind="none",
                artifacts=arts,
            )

        api_key = (result.api_key or "").strip()
        if not api_key:
            try:
                source.release(mailbox, success=False)
            except Exception:
                pass
            arts["fail_step"] = arts.get("fail_step") or "api_key"
            return RegisterResult(
                ok=False,
                provider=self.name,
                email=result.email or email,
                error="missing_api_key",
                error_kind="token",
                secret_kind="none",
                artifacts=arts,
            )

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            OUTPUT_DIR.chmod(0o700)
        except Exception:
            pass
        safe_email = (result.email or email).replace("@", "_at_").replace("/", "_")
        out_path = OUTPUT_DIR / (
            f"typesafe-{safe_email}-{time.time_ns()}-{uuid.uuid4().hex[:8]}.json"
        )
        try:
            save_result(result, out_path)
            arts["auth_path"] = str(out_path)
        except Exception as exc:
            arts["auth_write_error"] = str(exc)[:200]

        # Historical accounts.jsonl is not the success contract. Do not append
        # full api_key there; this-run 0600 json + pipeline sink own the secret.

        try:
            source.release(mailbox, success=True)
        except Exception:
            pass

        return RegisterResult(
            ok=True,
            provider=self.name,
            email=result.email or email,
            secret=api_key,
            secret_kind="api_key",
            artifacts={
                **arts,
                "api_key_id": result.api_key_id,
                "has_organization": bool(result.organization_id),
            },
        )
