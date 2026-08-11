"""Browser-free tests for the egress → Outlook browser identity resolver.

Monkeypatches ``_http_get`` so no network is touched. Synthetic ipinfo payloads
and proxy URLs are placeholders — the resolver never logs the proxy URL.
"""

from __future__ import annotations

import json

from register_core.providers import outlook_identity
from register_core.providers.outlook_identity import resolve_egress_identity


# ipinfo.io /json shape: country=ISO alpha-2, timezone=IANA, loc="lat,lng".
LA_PAYLOAD = json.dumps(
    {
        "ip": "155.254.126.21",
        "city": "Los Angeles",
        "region": "California",
        "country": "US",
        "org": "AS38136 Akari",
        "loc": "34.0522,-118.2437",
        "timezone": "America/Los_Angeles",
        "postal": "90001",
    }
)


def _patch_http_get(monkeypatch, body, status=200):
    captured: dict[str, object] = {}

    def fake(proxy, url, *, timeout):
        captured["proxy"] = proxy
        captured["url"] = url
        captured["timeout"] = timeout
        return (body, status, "curl_cffi")

    # resolve_egress_identity imports _http_get into its own module namespace, so
    # patch the name it actually looks up.
    monkeypatch.setattr(outlook_identity, "_http_get", fake)
    return captured


def test_resolver_returns_la_identity_for_us_ipinfo(monkeypatch):
    captured = _patch_http_get(monkeypatch, LA_PAYLOAD)

    identity = resolve_egress_identity("http://127.0.0.1:17897")

    assert identity == {
        "locale": "en-US",
        "timezone_id": "America/Los_Angeles",
        "latitude": 34.0522,
        "longitude": -118.2437,
    }
    # The lookup rides the registration proxy — exact proxy URL captured.
    assert captured["proxy"] == "http://127.0.0.1:17897"
    assert captured["url"] == "https://ipinfo.io/json"


def test_resolver_maps_non_table_country_to_en_us_fallback(monkeypatch):
    payload = json.dumps(
        {
            "ip": "203.0.113.5",
            "country": "XX",  # not in the country→locale table
            "loc": "1.3521,103.8198",
            "timezone": "Asia/Singapore",
        }
    )
    _patch_http_get(monkeypatch, payload)

    identity = resolve_egress_identity("http://example.invalid:7000")

    assert identity["locale"] == "en-US"
    assert identity["timezone_id"] == "Asia/Singapore"
    assert identity["latitude"] == 1.3521
    assert identity["longitude"] == 103.8198


def test_resolver_returns_empty_on_http_status_outage(monkeypatch):
    _patch_http_get(monkeypatch, "upstream error", status=503)

    identity = resolve_egress_identity("http://example.invalid:7000")

    assert identity == {}


def test_resolver_returns_empty_on_transport_exception(monkeypatch):
    def boom(_proxy, _url, *, timeout):
        raise OSError("synthetic transport failure")

    monkeypatch.setattr(outlook_identity, "_http_get", boom)

    identity = resolve_egress_identity("http://example.invalid:7000")

    assert identity == {}


def test_resolver_returns_empty_on_unparseable_body(monkeypatch):
    _patch_http_get(monkeypatch, "not-json-at-all", status=200)

    identity = resolve_egress_identity("http://example.invalid:7000")

    assert identity == {}


def test_resolver_returns_empty_when_country_missing(monkeypatch):
    payload = json.dumps({"ip": "203.0.113.5", "city": "Nowhere"})
    _patch_http_get(monkeypatch, payload)

    identity = resolve_egress_identity("http://example.invalid:7000")

    assert identity == {}


def test_resolver_strips_whitespace_proxy(monkeypatch):
    captured = _patch_http_get(monkeypatch, LA_PAYLOAD)

    resolve_egress_identity("  http://127.0.0.1:17897  ")

    assert captured["proxy"] == "http://127.0.0.1:17897"


def test_resolver_handles_partial_ipinfo_payload(monkeypatch):
    """Timezone or loc may be absent; the resolver must still return locale + the
    fields that are present, never the whole identity."""
    payload = json.dumps({"ip": "203.0.113.5", "country": "US"})
    _patch_http_get(monkeypatch, payload)

    identity = resolve_egress_identity("http://example.invalid:7000")

    assert identity == {"locale": "en-US"}


def test_resolver_tolerates_malformed_loc(monkeypatch):
    payload = json.dumps(
        {"ip": "203.0.113.5", "country": "US", "loc": "not-a-point", "timezone": "America/Chicago"}
    )
    _patch_http_get(monkeypatch, payload)

    identity = resolve_egress_identity("http://example.invalid:7000")

    assert identity == {"locale": "en-US", "timezone_id": "America/Chicago"}
