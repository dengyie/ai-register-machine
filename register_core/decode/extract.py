"""Single authoritative OTP-code decoder.

Moved out of ``register_core.email.sources.tinyhost`` (2026-07-18) so the
legacy ``grok_register_ttk.extract_verification_code`` could collapse onto
ONE decoder instead of maintaining a weaker duplicate. Two diverging copies
of the same decode was how the xAI ``#333333`` CSS-hex mis-decode slipped in:
the copy in the legacy path drifted from the one that actually strips
``<style>`` blocks.

Contract:
  - xAI real code is an alnum+dash ``XXX-XXX`` token (e.g. ``FN8-ECQ``) in the
    subject ("FN8-ECQ xAI confirmation code") and body.
  - OpenAI uses a 6-digit code inside "verification code" context.
  - typesafe.ai / jev uses a Stytch magic-link URL (see extract_typesafe_magic_link).
  - ``<style>{color:#333333}</style>`` must be stripped BEFORE any digit
    search, else the bare ``\\b(\\d{4,8})\\b`` fallback seizes ``333333`` and
    xAI rejects the form. See pxed smoke 2026-07-18.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlparse

OAI_SUBJECT_XAI_CODE_RE = re.compile(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", re.I)
XAI_BODY_CODE_RE = re.compile(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b")
# Kept for callers that want the bare 4-8 digit fallback; extract_otp_code goes
# through the contextual OpenAI patterns first to avoid CSS-hex false hits.
OTP_RE = re.compile(r"\b(\d{4,8})\b")
# Stytch magic-link used by typesafe.ai / jev console (not an OTP).
# Query order may drift; extract_typesafe_magic_link parses qs, not capture groups.
TYPESAFE_MAGIC_LINK_RE = re.compile(
    r"https://login\.typesafe\.ai/v1/magic_links/redirect\?[^\s\"'<>]+"
)
_OPENAI_OTP_PATTERNS = (
    re.compile(r"temporary\s+verification\s+code[^\d]{0,80}(\d{6})", re.I),
    re.compile(r"verification\s+code\s+to\s+continue[:\s]+(\d{6})", re.I),
    re.compile(r"verification\s+code[^\d]{0,40}(\d{4,8})", re.I),
    re.compile(r"your\s+(?:temporary\s+)?code[:\s]+(\d{4,8})", re.I),
    re.compile(r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})", re.I),
    re.compile(r"otp[^\d]{0,20}(\d{4,8})", re.I),
)


def extract_otp_code(blob: str, subject: str = "") -> str:
    """Extract a real OTP code from a decoded email (xAI ``XXX-XXX`` or OpenAI 6-digit)."""
    # 1. xAI subject-style "FN8-ECQ xAI confirmation code".
    if subject:
        m = OAI_SUBJECT_XAI_CODE_RE.search(str(subject))
        if m:
            return m.group(1)
    raw = str(blob or "")
    # 2. xAI body token (works on raw HTML too — alnum+dash isn't in CSS).
    m = XAI_BODY_CODE_RE.search(raw)
    if m:
        return m.group(1)
    # 3+4. OpenAI/numeric — strip style/script/comments FIRST so CSS hex
    # colors like #333333 / #888888 never win a 6-digit hit.
    if "<" in raw and ">" in raw:
        raw = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", raw)
        raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
        raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\s+", " ", raw)
    m = XAI_BODY_CODE_RE.search(raw)
    if m:
        return m.group(1)
    for pat in _OPENAI_OTP_PATTERNS:
        m = pat.search(raw)
        if m:
            return m.group(1)
    # Subject-aligned 6-digit fallback (OpenAI subject context).
    if subject and re.search(r"openai|verification code", subject, re.I):
        m = re.search(r"\b(\d{6})\b", raw)
        if m:
            return m.group(1)
    return ""


def extract_typesafe_magic_link(blob: str, subject: str = "") -> dict[str, str]:
    """Extract the Stytch magic-link URL from a typesafe.ai login email.

    Returns ``{public_token, token, magic_link}`` or ``{}``. HTML entities
    and quoted-printable ``=\\n`` soft breaks are unwrapped first so the
    redirect URL still matches after mailbox HTML decoding.
    """
    hay = "\n".join([str(subject or ""), str(blob or "")])
    if not hay.strip():
        return {}
    hay = html.unescape(hay.replace("=\n", "").replace("=\r\n", ""))
    m = TYPESAFE_MAGIC_LINK_RE.search(hay)
    if not m:
        return {}
    magic_link = m.group(0)
    qs = parse_qs(urlparse(magic_link).query, keep_blank_values=False)
    public_token = str((qs.get("public_token") or [""])[0] or "").strip()
    token = str((qs.get("token") or [""])[0] or "").strip()
    if not public_token or not token:
        return {}
    return {
        "public_token": public_token,
        "token": token,
        "magic_link": magic_link,
    }
