"""Mailbox provider protocol — allocate / release only."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from register_core.contracts import Mailbox


@runtime_checkable
class MailboxProvider(Protocol):
    """Creates one address per registration attempt (no OTP decoding)."""

    name: str

    def allocate(self) -> Mailbox:
        """Reserve or create one mailbox for a single attempt."""
        ...

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        """Optional cleanup / return to pool. Default no-op OK."""
        ...
