"""Adapters: wrap legacy EmailSource as OtpDecoder."""

from __future__ import annotations

from typing import Any

from register_core.contracts import Mailbox, OtpCode
from register_core.email.base import EmailSource


class EmailSourceDecoder:
    """OtpDecoder view over a full EmailSource backend."""

    def __init__(self, source: EmailSource, *, name: str | None = None) -> None:
        self._source = source
        self.name = name or getattr(source, "name", "email")
        self.last_wait_diagnostics = None

    def wait_otp(
        self,
        mailbox: Mailbox,
        *,
        timeout_s: float = 180,
        poll_interval_s: float = 3,
        used_codes: set[str] | None = None,
        newer_than_epoch: float | None = None,
        sender_hint: str | None = None,
    ) -> OtpCode:
        otp = self._source.poll_otp(
            mailbox,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            used_codes=used_codes,
            newer_than_epoch=newer_than_epoch,
            sender_hint=sender_hint,
        )
        self.last_wait_diagnostics = getattr(
            self._source, "last_wait_diagnostics", None
        )
        return otp

    @property
    def backend(self) -> EmailSource:
        return self._source
