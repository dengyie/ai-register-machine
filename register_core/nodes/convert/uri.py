"""Parse share-link URIs into Clash-style proxy dicts."""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


def _b64_decode(data: str) -> bytes:
    s = data.strip().replace("-", "+").replace("_", "/")
    pad = (-len(s)) % 4
    if pad:
        s += "=" * pad
    return base64.b64decode(s)


def _name_from_fragment(raw: str, fallback: str) -> str:
    if "#" in raw:
        frag = raw.split("#", 1)[1]
        return unquote(frag).strip() or fallback
    return fallback


def parse_uri(uri: str) -> dict[str, Any] | None:
    """Parse one share URI. Returns Clash-like dict or None if unsupported/invalid."""
    text = (uri or "").strip()
    if not text or text.startswith("#"):
        return None
    lower = text.lower()
    try:
        if lower.startswith("http://") or lower.startswith("https://"):
            return _parse_http_socks(text, default_type="http")
        if lower.startswith("socks5://") or lower.startswith("socks5h://") or lower.startswith("socks4://") or lower.startswith("socks://"):
            return _parse_http_socks(text)
        if lower.startswith("ss://"):
            return _parse_ss(text)
        if lower.startswith("vmess://"):
            return _parse_vmess(text)
        if lower.startswith("vless://"):
            return _parse_vless(text)
        if lower.startswith("trojan://"):
            return _parse_trojan(text)
    except Exception:
        return None
    return None


def parse_uri_lines(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        p = parse_uri(line)
        if p:
            out.append(p)
    return out


def _parse_http_socks(uri: str, default_type: str | None = None) -> dict[str, Any] | None:
    u = urlparse(uri)
    scheme = (u.scheme or default_type or "http").lower()
    if scheme in ("socks",):
        scheme = "socks5"
    host = u.hostname
    port = u.port
    if not host or not port:
        return None
    name = _name_from_fragment(uri, f"{scheme}-{host}-{port}")
    d: dict[str, Any] = {
        "name": name,
        "type": scheme if scheme != "https" else "http",
        "server": host,
        "port": int(port),
    }
    if u.username:
        d["username"] = unquote(u.username)
    if u.password:
        d["password"] = unquote(u.password)
    return d


def _parse_ss(uri: str) -> dict[str, Any] | None:
    # ss://BASE64(method:password@host:port)#name  OR ss://method:password@host:port
    raw = uri[5:]
    name = _name_from_fragment(uri, "ss")
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    if "@" not in raw:
        # fully base64
        try:
            decoded = _b64_decode(raw).decode("utf-8", errors="replace")
        except Exception:
            return None
        raw = decoded
    else:
        userinfo, hostinfo = raw.rsplit("@", 1)
        if ":" not in userinfo or (not userinfo.startswith("aes") and ":" in userinfo and not re.match(r"^[A-Za-z0-9+/=_-]+:", userinfo)):
            # userinfo may be base64(method:password)
            try:
                userinfo = _b64_decode(userinfo).decode("utf-8", errors="replace")
            except Exception:
                pass
        raw = f"{userinfo}@{hostinfo}"
    m = re.match(r"^(?P<method>[^:]+):(?P<password>.+)@(?P<host>[^:]+):(?P<port>\d+)$", raw)
    if not m:
        # try method:password@host:port with password containing :
        m = re.match(r"^(?P<method>[^:]+):(?P<password>.+)@(?P<host>\[[^\]]+\]|[^:]+):(?P<port>\d+)$", raw)
    if not m:
        return None
    return {
        "name": name if name != "ss" else f"ss-{m.group('host')}-{m.group('port')}",
        "type": "ss",
        "server": m.group("host").strip("[]"),
        "port": int(m.group("port")),
        "cipher": m.group("method"),
        "password": m.group("password"),
    }


def _parse_vmess(uri: str) -> dict[str, Any] | None:
    raw = uri[8:]
    if "#" in raw:
        raw = raw.split("#", 1)[0]
    try:
        data = json.loads(_b64_decode(raw).decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    host = str(data.get("add") or data.get("host") or "").strip()
    port = data.get("port")
    uuid = str(data.get("id") or "").strip()
    if not host or not port or not uuid:
        return None
    name = str(data.get("ps") or data.get("name") or f"vmess-{host}-{port}").strip()
    net = str(data.get("net") or "tcp").strip()
    tls = str(data.get("tls") or "").strip()
    d: dict[str, Any] = {
        "name": name,
        "type": "vmess",
        "server": host,
        "port": int(port),
        "uuid": uuid,
        "alterId": int(data.get("aid") or data.get("alterId") or 0),
        "cipher": str(data.get("scy") or data.get("security") or "auto"),
        "network": net,
    }
    if tls in ("tls", "true", "1"):
        d["tls"] = True
        if data.get("sni"):
            d["servername"] = str(data["sni"])
    if net == "ws":
        d["ws-opts"] = {
            "path": str(data.get("path") or "/"),
            "headers": {"Host": str(data.get("host") or host)},
        }
    return d


def _parse_vless(uri: str) -> dict[str, Any] | None:
    # vless://uuid@host:port?type=ws&security=tls&...#name
    u = urlparse(uri)
    host = u.hostname
    port = u.port
    uuid = unquote(u.username or "")
    if not host or not port or not uuid:
        return None
    q = {k: v[0] for k, v in parse_qs(u.query).items() if v}
    name = unquote(u.fragment) if u.fragment else f"vless-{host}-{port}"
    network = q.get("type") or q.get("network") or "tcp"
    security = (q.get("security") or "").lower()
    d: dict[str, Any] = {
        "name": name,
        "type": "vless",
        "server": host,
        "port": int(port),
        "uuid": uuid,
        "network": network,
        "udp": True,
    }
    if security in ("tls", "reality"):
        d["tls"] = True
        if q.get("sni"):
            d["servername"] = q["sni"]
        if security == "reality":
            d["reality-opts"] = {
                "public-key": q.get("pbk") or "",
                "short-id": q.get("sid") or "",
            }
            if q.get("fp"):
                d["client-fingerprint"] = q["fp"]
            if q.get("flow"):
                d["flow"] = q["flow"]
    if network == "ws":
        d["ws-opts"] = {
            "path": q.get("path") or "/",
            "headers": {"Host": q.get("host") or host},
        }
    if network == "grpc" and q.get("serviceName"):
        d["grpc-opts"] = {"grpc-service-name": q["serviceName"]}
    return d


def _parse_trojan(uri: str) -> dict[str, Any] | None:
    u = urlparse(uri)
    host = u.hostname
    port = u.port
    password = unquote(u.username or "")
    if not host or not port or not password:
        return None
    q = {k: v[0] for k, v in parse_qs(u.query).items() if v}
    name = unquote(u.fragment) if u.fragment else f"trojan-{host}-{port}"
    d: dict[str, Any] = {
        "name": name,
        "type": "trojan",
        "server": host,
        "port": int(port),
        "password": password,
        "udp": True,
    }
    if q.get("sni") or q.get("peer"):
        d["sni"] = q.get("sni") or q.get("peer")
    network = q.get("type") or q.get("network")
    if network == "ws":
        d["network"] = "ws"
        d["ws-opts"] = {
            "path": q.get("path") or "/",
            "headers": {"Host": q.get("host") or host},
        }
    return d
