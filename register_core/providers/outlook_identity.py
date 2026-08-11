"""Egress → Outlook browser identity resolver.

Microsoft's risk engine scores the browser identity (``locale``, ``timezone_id``,
geolocation) against the request's source IP. A spoofed US/LA residential exit
presenting a Chinese UI on a UTC clock with no geolocation is a textbook high-risk
flag — see memory ``outlook-hold-not-ip-challenge-fail-2026-08-08``.

This module resolves the identity the outgoing session *should* present by
probing ipinfo.io ``/json`` **through the registration proxy** — so the identity
matches the exact egress Microsoft scores on the signup request. The resolver is
pure (no browser), proxy-aware, and **never raises**: any outage falls back to an
empty dict, and the caller keeps its profile/default identity.

Security: the egress IP and ipinfo response are not secret, but the proxy URL
that carried the lookup must never reach a log line. ``_http_get`` is reused from
``register_core.nodes.health`` so the lookup rides the same curl_cffi→urllib stack
as the egress probe and never copies the proxy URL into a structured log.
"""

from __future__ import annotations

import json
from typing import Any

from register_core.nodes.health import _http_get

# ipinfo.io /json returns city/region/country/org/hostname/loc "lat,lng"/timezone
# (IANA, e.g. "America/Los_Angeles")/postal. Only the fields below are consumed.
IPINFO_JSON_URL = "https://ipinfo.io/json"

# Tiny country → locale table. The residential pool used today is US/LA, so the
# the default entry covers it. Countries outside the table fall back to en-US
# (a neutral western locale that matches the predominantly US residential pool);
# an authored ``locale`` in outlook_options always overrides this table.
_COUNTRY_TO_LOCALE: dict[str, str] = {
    "US": "en-US",
    "GB": "en-GB",
    "CA": "en-CA",
    "AU": "en-AU",
    "JP": "ja-JP",
    "KR": "ko-KR",
    "DE": "de-DE",
    "FR": "fr-FR",
    "TW": "zh-TW",
    "HK": "zh-HK",
}


def _locale_for_country(country: str) -> str:
    """Map an ISO-3166 alpha-2 country code to a locale; non-table → en-US."""
    code = (country or "").strip().upper()
    return _COUNTRY_TO_LOCALE.get(code, "en-US")


def resolve_egress_identity(
    proxy: str,
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """Return egress-derived identity ``{locale, timezone_id, latitude, longitude}``.

    Probes ipinfo.io ``/json`` **through ``proxy``** so the identity matches the
    exact egress Microsoft scores. On any failure (transport, status, parse) this
    returns ``{}`` — it **never raises**; the caller falls back to its
    profile/default identity rather than blocking a registration on a geo-lookup
    outage.

    The proxy URL is passed to ``_http_get`` and is never placed in the returned
    dict or in any structured record (caller never logs it).
    """
    proxy = (proxy or "").strip()
    try:
        body, status, _backend = _http_get(proxy, IPINFO_JSON_URL, timeout=timeout)
    except Exception:
        # _http_get is itself non-raising, but defend against any future variant.
        return {}
    if status is None or not (200 <= int(status) < 300):
        return {}
    try:
        data = json.loads(body or "")
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict) or not data:
        return {}

    country = str(data.get("country") or "").strip().upper()
    if not country:
        return {}

    identity: dict[str, Any] = {"locale": _locale_for_country(country)}

    tz = str(data.get("timezone") or "").strip()
    if tz:
        identity["timezone_id"] = tz

    loc = str(data.get("loc") or "").strip()
    if loc and "," in loc:
        lat_str, _sep, lng_str = loc.partition(",")
        try:
            lat = float(lat_str.strip())
            lng = float(lng_str.strip())
        except ValueError:
            lat = lng = None
        if lat is not None and lng is not None:
            identity["latitude"] = lat
            identity["longitude"] = lng

    return identity
