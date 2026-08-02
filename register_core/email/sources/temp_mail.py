"""Cloudflare Temp Mail source (``cf_temp``) behind the EmailSource protocol.

Adapted from daimon3332/OutlookRegister (MIT):
https://github.com/daimon3332/OutlookRegister

This source is isolated from ``mail_assets`` / Hotmail Graph REST: it allocates
brand-new throwaway addresses for Outlook recovery binding and OAuth proof, and
never acts as a fallback for the existing pooled sources.

Security: the admin password is read from a named environment variable at call
time. Neither the password, the mailbox JWT, nor raw message bodies are logged
or stored on diagnostics.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any

import httpx

from register_core.contracts import Mailbox, OtpCode, OtpWaitDiagnostics
from register_core.errors import MailMissError, ProviderError

_CODE_PATTERNS = (
    re.compile(r"(?i)(?:code|验证码|security code)[^0-9]{0,20}(\d{6})"),
    re.compile(r"\b(\d{6})\b"),
)

_MAIL_TIME_KEYS = ("timestamp", "created_at", "createdAt", "date", "received_at")


def _mail_epoch(mail: dict[str, Any]) -> float | None:
    """Best-effort epoch seconds for one mail row; None when undeterminable."""
    for key in _MAIL_TIME_KEYS:
        raw = mail.get(key)
        if raw in (None, ""):
            continue
        if isinstance(raw, (int, float)):
            value = float(raw)
            # CF Temp Mail sometimes reports milliseconds.
            return value / 1000.0 if value > 1e11 else value
        text = str(raw).strip()
        try:
            return float(text)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return None


class TempMailClient:
    """Thin HTTP client for a self-hosted CF Temp Mail deployment."""

    def __init__(
        self,
        *,
        base_url: str,
        admin_password_env: str,
        domain: str,
        name_prefix: str = "orx",
        enable_prefix: bool = True,
        timeout: float = 30,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.admin_password_env = admin_password_env
        self.domain = domain
        self.name_prefix = name_prefix
        self.enable_prefix = enable_prefix
        self.timeout = float(timeout)
        self.last_wait_diagnostics: OtpWaitDiagnostics | None = None

    @staticmethod
    def extract_code_from_text(text: str) -> str | None:
        for pattern in _CODE_PATTERNS:
            match = pattern.search(text or "")
            if match:
                return match.group(1)
        return None

    @staticmethod
    def mailbox_from_payload(payload: dict[str, Any]) -> Mailbox:
        """Build a Mailbox from ``/admin/new_address`` JSON (no meta, no secrets)."""
        address = str(payload.get("address") or "").strip()
        if not address:
            raise ProviderError("temp_mail_address_missing")
        return Mailbox(
            address=address,
            token=str(payload.get("jwt") or payload.get("token") or ""),
            provider="cf_temp",
        )

    def _admin_password(self) -> str:
        password = os.environ.get(self.admin_password_env, "")
        if not password:
            raise ProviderError("temp_mail_admin_env_missing")
        return password

    def create_address(self) -> Mailbox:
        if not self.base_url:
            raise ProviderError("temp_mail_base_url_missing")
        try:
            response = httpx.post(
                f"{self.base_url}/admin/new_address",
                headers={"x-admin-auth": self._admin_password()},
                json={
                    "domain": self.domain,
                    "prefix": self.name_prefix if self.enable_prefix else "",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"temp_mail_allocate_failed: {type(exc).__name__}") from exc
        return self.mailbox_from_payload(payload)

    def list_mails(self, mailbox: Mailbox) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{self.base_url}/api/mails",
            headers={"Authorization": f"Bearer {mailbox.token}"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        rows = payload.get("mails") or payload.get("results") or []
        return [row for row in rows if isinstance(row, dict)]

    def wait_for_code(
        self,
        mailbox: Mailbox,
        *,
        timeout_sec: float,
        poll_sec: float,
        after_ts: float | None = None,
        used_codes: set[str] | None = None,
        sender_hint: str | None = None,
    ) -> OtpCode:
        """Poll until a fresh 6-digit code arrives, else raise MailMissError."""
        diag = OtpWaitDiagnostics(
            timeout_s=float(timeout_sec),
            provider="cf_temp",
            sender_hint=sender_hint or "",
            notes="cf_temp wait_for_code",
        )
        self.last_wait_diagnostics = diag
        seen = used_codes or set()
        started = time.monotonic()
        deadline = started + float(timeout_sec)

        while True:
            diag.poll_count += 1
            try:
                mails = self.list_mails(mailbox)
            except Exception as exc:
                diag.failure_class = "imap_error"
                diag.elapsed_seconds = time.monotonic() - started
                raise MailMissError(
                    f"cf_temp mail list failed for {mailbox.address}: {type(exc).__name__}",
                    diagnostics=diag,
                ) from exc

            if after_ts is not None:
                mails = [
                    mail
                    for mail in mails
                    if (_mail_epoch(mail) or 0.0) == 0.0 or (_mail_epoch(mail) or 0.0) > after_ts
                ]
            if mails:
                diag.message_scan_count += len(mails)
                if diag.first_message_seen_at is None:
                    diag.first_message_seen_at = time.monotonic()
                    diag.first_seen_after_seconds = diag.first_message_seen_at - started
            else:
                diag.empty_rounds += 1

            if sender_hint:
                hint = sender_hint.lower()
                preferred = [
                    mail
                    for mail in mails
                    if hint in f"{mail.get('source', '')}{mail.get('from', '')}".lower()
                ]
                mails = preferred + [mail for mail in mails if mail not in preferred]

            for mail in mails:
                haystack = " ".join(
                    str(mail.get(key, "") or "") for key in ("subject", "text", "body")
                )
                code = self.extract_code_from_text(haystack)
                if not code:
                    continue
                if code in seen:
                    diag.failure_class = diag.failure_class or "stale_code"
                    continue
                diag.failure_class = ""
                diag.matched_at = time.monotonic()
                diag.matched_after_seconds = diag.matched_at - started
                diag.elapsed_seconds = diag.matched_at - started
                return OtpCode(code=code, source="cf_temp")

            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.1, float(poll_sec)))

        diag.failure_class = diag.failure_class or ("parse_fail" if diag.message_scan_count else "no_mail")
        diag.elapsed_seconds = time.monotonic() - started
        raise MailMissError(
            f"cf_temp no OTP for {mailbox.address} before deadline",
            diagnostics=diag,
        )

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        """CF Temp Mail addresses are disposable; nothing to return to a pool."""
        return None


class TempMailSource:
    """EmailSource adapter over TempMailClient, registered as temp_mail/cf_temp."""

    name = "cf_temp"

    def __init__(self, client: TempMailClient | None = None, **options: Any) -> None:
        if client is not None and options:
            raise ProviderError("temp_mail_ambiguous_construction")
        self.client = client if client is not None else _build_client(options)
        self.last_wait_diagnostics: OtpWaitDiagnostics | None = None

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> "TempMailSource":
        return cls(_build_client(options or {}))

    def _mailbox_from_payload(self, payload: dict[str, Any]) -> Mailbox:
        return self.client.mailbox_from_payload(payload)

    def allocate(self) -> Mailbox:
        return self.client.create_address()

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
        try:
            return self.client.wait_for_code(
                mailbox,
                timeout_sec=timeout_s,
                poll_sec=poll_interval_s,
                after_ts=newer_than_epoch,
                used_codes=used_codes,
                sender_hint=sender_hint,
            )
        finally:
            self.last_wait_diagnostics = self.client.last_wait_diagnostics

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        self.client.release(mailbox, success=success)


def _build_client(options: dict[str, Any]) -> TempMailClient:
    return TempMailClient(
        base_url=str(options.get("base_url") or ""),
        admin_password_env=str(options.get("admin_password_env") or "CF_TEMP_ADMIN_ENV"),
        domain=str(options.get("domain") or ""),
        name_prefix=str(options.get("name_prefix") or "orx"),
        enable_prefix=bool(options.get("enable_prefix", True)),
        timeout=float(options.get("timeout", 30)),
    )
