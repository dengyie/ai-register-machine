"""Shared type sets for dialable vs protocol proxies."""

from __future__ import annotations

# curl_cffi can dial these as proxy=URL without a core
DIALABLE_TYPES = frozenset({"http", "https", "socks5", "socks5h", "socks4", "socks"})

# need mihomo (or similar) mixed-port
PROTOCOL_TYPES = frozenset(
    {
        "ss",
        "ssr",
        "vmess",
        "vless",
        "trojan",
        "hysteria",
        "hysteria2",
        "tuic",
        "wireguard",
        "snell",
        "anytls",
        "mieru",
        "shadowtls",
        "ssh",
    }
)

# required fields by Clash proxy type (minimal legality)
REQUIRED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "http": ("server", "port"),
    "https": ("server", "port"),
    "socks5": ("server", "port"),
    "socks5h": ("server", "port"),
    "socks4": ("server", "port"),
    "socks": ("server", "port"),
    "ss": ("server", "port", "cipher", "password"),
    "ssr": ("server", "port", "cipher", "password", "protocol", "obfs"),
    "vmess": ("server", "port", "uuid"),
    "vless": ("server", "port", "uuid"),
    "trojan": ("server", "port", "password"),
    "hysteria": ("server", "port"),
    "hysteria2": ("server", "port", "password"),
    "tuic": ("server", "port", "uuid"),
    "wireguard": ("server", "port", "private-key"),
    "snell": ("server", "port", "psk"),
    "anytls": ("server", "port", "password"),
}

DEFAULT_MIXED_PORT = 17897
DEFAULT_CONTROLLER = "127.0.0.1:19097"
DEFAULT_GROUP = "REGISTER"
