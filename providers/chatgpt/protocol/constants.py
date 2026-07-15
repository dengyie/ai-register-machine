"""OpenAI auth / platform OAuth constants (protocol register path)."""

from __future__ import annotations

AUTH_BASE = "https://auth.openai.com"
PLATFORM_BASE = "https://platform.openai.com"
SENTINEL_BASE = "https://sentinel.openai.com"

# Platform OAuth client used by open-reg-auto / zhuce6-style protocol path.
PLATFORM_OAUTH_CLIENT_ID = "app_2SKx67EdpoN0G6j64rFvigXD"
PLATFORM_OAUTH_REDIRECT_URI = f"{PLATFORM_BASE}/auth/callback"
PLATFORM_OAUTH_AUDIENCE = "https://api.openai.com/v1"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
SEC_CH_UA = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'
SEC_CH_UA_FULL = (
    '"Chromium";v="145.0.0.0", "Not:A-Brand";v="99.0.0.0", '
    '"Google Chrome";v="145.0.0.0"'
)

DEFAULT_TIMEOUT = 30
SENTINEL_SDK = "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js"

COMMON_HEADERS: dict[str, str] = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": AUTH_BASE,
    "priority": "u=1, i",
    "user-agent": USER_AGENT,
    "sec-ch-ua": SEC_CH_UA,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": SEC_CH_UA_FULL,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

NAVIGATE_HEADERS: dict[str, str] = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": USER_AGENT,
    "sec-ch-ua": SEC_CH_UA,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": SEC_CH_UA_FULL,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}
