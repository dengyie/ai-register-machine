"""Mailbox allocation layer (address only; OTP decode is separate)."""

from register_core.mailbox.base import MailboxProvider
from register_core.mailbox.registry import get_mailbox_provider, list_mailbox_providers

__all__ = [
    "MailboxProvider",
    "get_mailbox_provider",
    "list_mailbox_providers",
]
