"""Adapters: wrap legacy EmailSource as MailboxProvider (and shared backend)."""

from __future__ import annotations

from typing import Any

from register_core.contracts import Mailbox
from register_core.email.base import EmailSource


class EmailSourceMailbox:
    """MailboxProvider view over a full EmailSource backend."""

    def __init__(self, source: EmailSource, *, name: str | None = None) -> None:
        self._source = source
        self.name = name or getattr(source, "name", "email")
        # Surface common knobs for adapters that mutate source after inject.
        if hasattr(source, "proxy"):
            self.proxy = getattr(source, "proxy")
        if hasattr(source, "forced_domain"):
            self.forced_domain = getattr(source, "forced_domain")

    def allocate(self) -> Mailbox:
        return self._source.allocate()

    def release(self, mailbox: Mailbox, *, success: bool) -> None:
        self._source.release(mailbox, success=success)

    @property
    def backend(self) -> EmailSource:
        return self._source

    def __setattr__(self, key: str, value: Any) -> None:
        # Keep tinyhost forced_domain / proxy in sync with backend.
        if key in ("proxy", "forced_domain") and "_source" in self.__dict__:
            try:
                setattr(self._source, key, value)
            except Exception:
                pass
        super().__setattr__(key, value)
