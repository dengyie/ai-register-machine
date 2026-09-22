"""typesafe.ai console constants (Stytch magic-link + Next.js Server Action)."""

from __future__ import annotations

CONSOLE_BASE_URL = "https://console.typesafe.ai"
LOGIN_PAGE_URL = f"{CONSOLE_BASE_URL}/login"
# Fallback Next.js deployment id from the original public login snapshot.
# Live HTML is authoritative (pxed 2026-09-22: fb80dfc7f55f4bf7a496bc9579755816f26fae3c);
# mismatch is logged; the page $ACTION blob is always preferred.
CONSOLE_DEPLOYMENT_ID = "cc6f6dca06537cc04123caaaf50ca5a76d506a92"

DEFAULT_API_KEY_NAME = "register-core"
DEFAULT_TIMEOUT = 30
TLS_ECDH_CURVE = "prime256v1"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0"
)

COMMON_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.7",
}
