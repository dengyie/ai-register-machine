"""Cloudflare Worker temp-mail (cloudflare_temp_email) via grok_register_ttk."""

from __future__ import annotations

import time as _time
from typing import Any

from register_core.contracts import Mailbox, OtpCode, OtpWaitDiagnostics
from register_core.errors import FailFastError, MailMissError, ProviderError


class CloudflareSource:
    """Allocate + poll OpenAI OTP through the project's Cloudflare Worker.

    Uses the same helpers as Grok ``email_provider=cloudflare``
    (POST /api/new_address → JWT, poll /api/mails). Config comes from
    ``config.json`` / env overlays on ``grok_register_ttk`` (``cloudflare_api_base``,
    optional key/auth_mode).
    """

    name = "cloudflare"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.last_wait_diagnostics = None

    def _reg(self):
        try:
            import grok_register_ttk as reg  # type: ignore
        except Exception as exc:
            raise FailFastError(f"cloudflare requires grok_register_ttk: {exc}") from exc
        # Ensure config.json / .env overlays are applied (import may have been stubbed).
        try:
            if hasattr(reg, "load_config"):
                reg.load_config()
        except Exception:
            pass
        return reg

    def allocate(self) -> Mailbox:
        reg = self._reg()
        base = ""
        try:
            if hasattr(reg, "get_cloudflare_api_base"):
                base = str(reg.get_cloudflare_api_base() or "").strip()
            if not base:
                base = str(reg.config.get("cloudflare_api_base", "") or "").strip()
        except Exception:
            base = ""
        if not base:
            raise FailFastError(
                "cloudflare email source requires cloudflare_api_base "
                "(config.json or CLOUDFLARE_API_BASE)"
            )
        prev = None
        try:
            prev = reg.config.get("email_provider")
            reg.config["email_provider"] = "cloudflare"
            address, token = reg.get_email_and_token()
        except FailFastError:
            raise
        except Exception as exc:
            raise ProviderError(f"cloudflare allocate failed: {exc}") from exc
        finally:
            if prev is not None:
                reg.config["email_provider"] = prev
        return Mailbox(address=address, token=token or "", provider=self.name)

    def poll_otp(
        self,
        mailbox: Mailbox,
        *,
        timeout_s: float = 180,
        poll_interval_s: float = 3,
        used_codes: set[str] | None = None,
        newer_than_epoch: float | None = None,
        sender_hint: str | None = None,
    ) -> OtpCode:
        started = _time.time()
        diag = OtpWaitDiagnostics(
            timeout_s=float(timeout_s),
            provider=self.name,
            sender_hint=(sender_hint or ""),
            notes="wraps grok_register_ttk.cloudflare_get_oai_code",
        )
        self.last_wait_diagnostics = diag
        reg = self._reg()
        prev = None
        code = None
        try:
            prev = reg.config.get("email_provider")
            reg.config["email_provider"] = "cloudflare"
            diag.poll_count = 1
            code = reg.get_oai_code(
                mailbox.token,
                mailbox.address,
                timeout=int(timeout_s),
                poll_interval=int(poll_interval_s),
            )
        except Exception as exc:
            msg = str(exc)
            low = msg.lower()
            if "timeout" in low or "未收到" in msg or "no mail" in low or "empty" in low:
                diag.failure_class = "no_mail"
            elif "auth" in low or "401" in low or "403" in low:
                diag.failure_class = "imap_error"
                diag.notes = ((diag.notes or "") + " auth_fail").strip()
            else:
                diag.failure_class = "imap_error"
                diag.notes = ((diag.notes or "") + f" transport:{msg[:80]}").strip()
            diag.elapsed_seconds = _time.time() - started
            self.last_wait_diagnostics = diag
            raise MailMissError(f"cloudflare OTP failed: {exc}", diagnostics=diag) from exc
        finally:
            if prev is not None:
                reg.config["email_provider"] = prev
        diag.elapsed_seconds = _time.time() - started
        if not code:
            diag.failure_class = "no_mail"
            self.last_wait_diagnostics = diag
            raise MailMissError(
                f"cloudflare empty OTP for {mailbox.address}",
                diagnostics=diag,
            )
        if used_codes and code in used_codes:
            diag.failure_class = "stale_code"
            self.last_wait_diagnostics = diag
            raise MailMissError(
                f"cloudflare OTP already used for {mailbox.address}",
                diagnostics=diag,
            )
        diag.matched_at = _time.time()
        diag.matched_after_seconds = diag.matched_at - started
        diag.message_scan_count = 1
        self.last_wait_diagnostics = diag
        return OtpCode(code=str(code), source=self.name)

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        return
