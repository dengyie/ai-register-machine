"""OTP decoder protocol — wait for code on an already-allocated mailbox."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from register_core.contracts import Mailbox, OtpCode


@runtime_checkable
class OtpDecoder(Protocol):
    """Pull messages for a mailbox and parse an OTP."""

    name: str

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
        """Block until a fresh OTP arrives or raise MailMissError."""
        ...
