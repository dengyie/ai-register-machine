"""Mailbox provider factories (paired backends via email sources for M1)."""

from __future__ import annotations

from typing import Any, Callable

from register_core.email.registry import get_email_source, list_email_sources
from register_core.mailbox.adapters import EmailSourceMailbox
from register_core.mailbox.base import MailboxProvider

_ALIASES = {
    "cf": "cloudflare",
    "cloudflare_worker": "cloudflare",
    "gmail": "gmail_imap",
}


def list_mailbox_providers() -> list[str]:
    # M1: same names as email backends that can allocate.
    return sorted(set(list_email_sources()) | set(_ALIASES.keys()))


def get_mailbox_provider(name: str, **kwargs: Any) -> MailboxProvider:
    key = _ALIASES.get((name or "").strip().lower(), (name or "").strip().lower())
    if key in ("", "provider", "none", "internal"):
        raise KeyError("mailbox type 'provider' is adapter-internal; not a core mailbox")
    source = get_email_source(key, **kwargs)
    return EmailSourceMailbox(source, name=key)
