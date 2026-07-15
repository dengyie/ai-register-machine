"""Detect and parse Clash YAML / V2Ray JSON / URI list into proxy dicts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from register_core.nodes.convert.uri import parse_uri, parse_uri_lines

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore


class ParseError(ValueError):
    def __init__(self, message: str, *, format: str = "unknown", source: str = ""):
        super().__init__(message)
        self.format = format
        self.source = source


def detect_format(text: str, *, filename: str = "") -> str:
    """Return clash_yaml | v2ray_json | uri_list | unknown."""
    name = (filename or "").lower()
    stripped = (text or "").lstrip("﻿").strip()
    if not stripped:
        return "unknown"
    if name.endswith((".yaml", ".yml")):
        return "clash_yaml"
    if name.endswith(".json"):
        # could be v2ray or clash-json
        try:
            data = json.loads(stripped)
        except Exception:
            return "unknown"
        if isinstance(data, dict) and ("outbounds" in data or "outbound" in data):
            return "v2ray_json"
        if isinstance(data, dict) and isinstance(data.get("proxies"), list):
            return "clash_yaml"
        if isinstance(data, list):
            return "v2ray_json"
        return "unknown"
    # content sniff
    first_lines = stripped.splitlines()[:8]
    joined = "\n".join(first_lines).lower()
    if any(
        line.strip().lower().startswith(("ss://", "vmess://", "vless://", "trojan://", "socks", "http://", "https://"))
        for line in first_lines
        if line.strip() and not line.strip().startswith("#")
    ):
        return "uri_list"
    if "proxies:" in joined or "proxy-groups:" in joined:
        return "clash_yaml"
    if stripped[0] in "{[":
        try:
            data = json.loads(stripped)
        except Exception:
            return "unknown"
        if isinstance(data, dict) and ("outbounds" in data or "outbound" in data):
            return "v2ray_json"
        if isinstance(data, dict) and isinstance(data.get("proxies"), list):
            return "clash_yaml"
    return "unknown"


def parse_text(text: str, *, source: str = "", format_hint: str = "") -> tuple[str, list[dict[str, Any]]]:
    """Parse text → (format, proxies). Raises ParseError if empty/unusable."""
    fmt = (format_hint or detect_format(text, filename=source)).lower()
    if fmt in ("auto", "", "unknown"):
        fmt = detect_format(text, filename=source)
    if fmt == "clash_yaml":
        return fmt, _parse_clash(text, source=source)
    if fmt == "v2ray_json":
        return fmt, _parse_v2ray(text, source=source)
    if fmt == "uri_list":
        proxies = parse_uri_lines(text)
        if not proxies:
            # try single line
            one = parse_uri(text.strip())
            proxies = [one] if one else []
        if not proxies:
            raise ParseError("no valid share URIs found", format=fmt, source=source)
        return fmt, proxies
    raise ParseError(
        f"unsupported or unrecognized format (hint={format_hint!r}); "
        "use Clash YAML, V2Ray JSON, or URI lines (ss/vmess/vless/trojan/http/socks)",
        format=fmt,
        source=source,
    )


def parse_file(path: Path, *, format_hint: str = "") -> tuple[str, list[dict[str, Any]]]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ParseError(f"not a file: {path}", source=str(path))
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_text(text, source=str(path), format_hint=format_hint or detect_format(text, filename=path.name))


def _parse_clash(text: str, *, source: str) -> list[dict[str, Any]]:
    if yaml is None:
        raise ParseError("PyYAML required for Clash YAML", format="clash_yaml", source=source)
    try:
        data = yaml.safe_load(text) or {}
    except Exception as exc:
        raise ParseError(f"invalid YAML: {exc}", format="clash_yaml", source=source) from exc
    if isinstance(data, list):
        proxies = data
    elif isinstance(data, dict):
        proxies = data.get("proxies") or []
    else:
        raise ParseError("YAML root must be mapping with proxies: or a list", format="clash_yaml", source=source)
    if not isinstance(proxies, list) or not proxies:
        raise ParseError("no proxies[] in Clash profile", format="clash_yaml", source=source)
    out: list[dict[str, Any]] = []
    for item in proxies:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str):
            p = parse_uri(item)
            if p:
                out.append(p)
    if not out:
        raise ParseError("proxies[] contained no objects", format="clash_yaml", source=source)
    return out


def _parse_v2ray(text: str, *, source: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except Exception as exc:
        raise ParseError(f"invalid JSON: {exc}", format="v2ray_json", source=source) from exc
    outbounds: list[Any]
    if isinstance(data, list):
        outbounds = data
    elif isinstance(data, dict):
        if isinstance(data.get("outbounds"), list):
            outbounds = data["outbounds"]
        elif isinstance(data.get("outbound"), dict):
            outbounds = [data["outbound"]]
        elif str(data.get("type") or data.get("protocol") or ""):
            outbounds = [data]
        else:
            raise ParseError("no outbounds in V2Ray JSON", format="v2ray_json", source=source)
    else:
        raise ParseError("V2Ray JSON root must be object or list", format="v2ray_json", source=source)

    out: list[dict[str, Any]] = []
    for i, ob in enumerate(outbounds):
        if not isinstance(ob, dict):
            continue
        protocol = str(ob.get("protocol") or ob.get("type") or "").lower().strip()
        # skip freedom/blackhole/dns/etc
        if protocol in ("freedom", "blackhole", "dns", "loopback", "dokodemo-door", ""):
            continue
        tag = str(ob.get("tag") or ob.get("name") or f"{protocol}-{i}").strip()
        settings = ob.get("settings") if isinstance(ob.get("settings"), dict) else {}
        stream = ob.get("streamSettings") if isinstance(ob.get("streamSettings"), dict) else {}
        converted = _v2ray_outbound_to_clash(protocol, tag, settings, stream, ob)
        if converted:
            out.append(converted)
    if not out:
        raise ParseError("no convertible proxy outbounds", format="v2ray_json", source=source)
    return out


def _v2ray_outbound_to_clash(
    protocol: str,
    tag: str,
    settings: dict[str, Any],
    stream: dict[str, Any],
    raw: dict[str, Any],
) -> dict[str, Any] | None:
    # already clash-shaped
    if raw.get("server") and raw.get("port") and raw.get("type"):
        d = dict(raw)
        d.setdefault("name", tag)
        return d

    vnext = settings.get("vnext") if isinstance(settings.get("vnext"), list) else []
    servers = settings.get("servers") if isinstance(settings.get("servers"), list) else []
    network = str(stream.get("network") or "tcp").lower()
    security = str(stream.get("security") or "").lower()

    if protocol in ("vmess", "vless") and vnext:
        node = vnext[0] if isinstance(vnext[0], dict) else {}
        users = node.get("users") if isinstance(node.get("users"), list) else []
        user = users[0] if users and isinstance(users[0], dict) else {}
        host = str(node.get("address") or "").strip()
        port = node.get("port")
        uuid = str(user.get("id") or "").strip()
        if not host or not port or not uuid:
            return None
        d: dict[str, Any] = {
            "name": tag,
            "type": protocol,
            "server": host,
            "port": int(port),
            "uuid": uuid,
            "network": network,
            "udp": True,
        }
        if protocol == "vmess":
            d["alterId"] = int(user.get("alterId") or 0)
            d["cipher"] = str(user.get("security") or "auto")
        if user.get("flow"):
            d["flow"] = str(user["flow"])
        if security in ("tls", "reality"):
            d["tls"] = True
            tls_set = stream.get("tlsSettings") or stream.get("realitySettings") or {}
            if isinstance(tls_set, dict):
                sni = tls_set.get("serverName") or (tls_set.get("serverNames") or [None])[0]
                if sni:
                    d["servername"] = str(sni)
                if security == "reality":
                    d["reality-opts"] = {
                        "public-key": str(tls_set.get("publicKey") or ""),
                        "short-id": str((tls_set.get("shortIds") or [""])[0] if isinstance(tls_set.get("shortIds"), list) else tls_set.get("shortId") or ""),
                    }
        if network == "ws":
            ws = stream.get("wsSettings") if isinstance(stream.get("wsSettings"), dict) else {}
            headers = ws.get("headers") if isinstance(ws.get("headers"), dict) else {}
            d["ws-opts"] = {
                "path": str(ws.get("path") or "/"),
                "headers": {"Host": str(headers.get("Host") or host)},
            }
        return d

    if protocol in ("trojan", "shadowsocks", "socks", "http") and servers:
        node = servers[0] if isinstance(servers[0], dict) else {}
        host = str(node.get("address") or node.get("server") or "").strip()
        port = node.get("port")
        if not host or not port:
            return None
        if protocol == "shadowsocks":
            return {
                "name": tag,
                "type": "ss",
                "server": host,
                "port": int(port),
                "cipher": str(node.get("method") or settings.get("method") or "aes-256-gcm"),
                "password": str(node.get("password") or ""),
            }
        if protocol == "trojan":
            d = {
                "name": tag,
                "type": "trojan",
                "server": host,
                "port": int(port),
                "password": str(node.get("password") or ""),
                "udp": True,
            }
            if security == "tls":
                tls_set = stream.get("tlsSettings") if isinstance(stream.get("tlsSettings"), dict) else {}
                if tls_set.get("serverName"):
                    d["sni"] = str(tls_set["serverName"])
            return d
        # socks / http
        d = {
            "name": tag,
            "type": "socks5" if protocol == "socks" else "http",
            "server": host,
            "port": int(port),
        }
        users = node.get("users") if isinstance(node.get("users"), list) else []
        if users and isinstance(users[0], dict):
            if users[0].get("user"):
                d["username"] = str(users[0]["user"])
            if users[0].get("pass"):
                d["password"] = str(users[0]["pass"])
        return d

    # single-server trojan style in some exports
    if protocol == "trojan" and settings.get("address"):
        return {
            "name": tag,
            "type": "trojan",
            "server": str(settings["address"]),
            "port": int(settings.get("port") or 443),
            "password": str(settings.get("password") or ""),
        }
    return None
