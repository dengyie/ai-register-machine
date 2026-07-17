"""OTP decoder factories (paired backends via email sources for M1)."""

from __future__ import annotations

from typing import Any

from register_core.decode.adapters import EmailSourceDecoder
from register_core.decode.base import OtpDecoder
from register_core.email.registry import get_email_source, list_email_sources

_ALIASES = {
    "cf": "cloudflare",
    "cloudflare_worker": "cloudflare",
    "gmail": "gmail_imap",
}


def list_otp_decoders() -> list[str]:
    return sorted(set(list_email_sources()) | set(_ALIASES.keys()))


def get_otp_decoder(name: str, **kwargs: Any) -> OtpDecoder:
    key = _ALIASES.get((name or "").strip().lower(), (name or "").strip().lower())
    if key in ("", "provider", "none", "internal"):
        raise KeyError("decode type 'provider' is adapter-internal; not a core decoder")
    source = get_email_source(key, **kwargs)
    return EmailSourceDecoder(source, name=key)
