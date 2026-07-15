"""Register-scoped egress selection for layered providers.

Self-controlled nodes: prefer ``list`` mode with explicit proxy URLs
(``PROXY_LIST`` / ``proxy_list``). That path never calls Clash to select a
node — each register attempt uses a concrete upstream from the pool.

Clash mode remains available for Grok browser paths that already use a
dedicated GROK-REG group, but in-process providers (ChatGPT) should default
to list / fixed URL.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

log = logging.getLogger("register_core.util.proxy")

LogFn = Callable[[str], None] | None

_CONFIGURED = False


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        val = os.environ.get(name)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return default


def rotation_config_from_env_and_extra(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build proxy_rotate.configure() dict from extra + env (CLI-friendly).

    Priority: extra keys > CHATGPT_* env > PROXY_* env > defaults.
    If a proxy list is present and mode is empty, auto-select ``list``.
    """
    extra = extra if isinstance(extra, dict) else {}

    mode_raw = str(
        extra.get("proxy_rotate_mode")
        if extra.get("proxy_rotate_mode") is not None
        else _env_first("CHATGPT_PROXY_ROTATE_MODE", "PROXY_ROTATE_MODE")
    ).strip().lower()
    mode_explicit = bool(mode_raw)
    mode = mode_raw
    if mode in {"none", "disabled", "0", "false", ""}:
        mode = "off"

    proxy_list = (
        extra.get("proxy_list")
        or extra.get("proxy_pool")
        or _env_first("CHATGPT_PROXY_LIST", "PROXY_LIST", "PROXY_POOL")
        or ""
    )
    base_proxy = str(
        extra.get("proxy")
        or _env_first(
            "CHATGPT_PROXY",
            "MIMO_PROXY",
            "https_proxy",
            "HTTPS_PROXY",
            "http_proxy",
            "HTTP_PROXY",
        )
        or ""
    ).strip()

    # Self-control default: unset mode + explicit pool ⇒ list (no Clash selector).
    # Explicit proxy_rotate_mode=off stays off even if a list is present.
    if not mode_explicit and proxy_list:
        mode = "list"
    if mode in {"proxy_list", "pool", "url", "urls"}:
        mode = "list"

    every_raw = extra.get("proxy_rotate_every")
    if every_raw is None or every_raw == "":
        every_raw = _env_first("CHATGPT_PROXY_ROTATE_EVERY", "PROXY_ROTATE_EVERY", default="1")
    try:
        every = max(1, int(every_raw))
    except Exception:
        every = 1

    required_raw = extra.get("proxy_rotate_required")
    if required_raw is None:
        required_raw = _env_first("CHATGPT_PROXY_ROTATE_REQUIRED", "PROXY_ROTATE_REQUIRED", default="")
    if isinstance(required_raw, bool):
        required = required_raw
    else:
        required = str(required_raw).strip().lower() in {"1", "true", "yes", "on"}

    on_start_raw = extra.get("proxy_rotate_on_start")
    if on_start_raw is None:
        on_start_raw = _env_first(
            "CHATGPT_PROXY_ROTATE_ON_START", "PROXY_ROTATE_ON_START", default=""
        )
    if on_start_raw is None or on_start_raw == "":
        on_start = mode in {"list", "clash"}
    elif isinstance(on_start_raw, bool):
        on_start = on_start_raw
    else:
        on_start = str(on_start_raw).strip().lower() in {"1", "true", "yes", "on"}

    cfg: dict[str, Any] = {
        "proxy_rotate_mode": mode,
        "proxy_rotate_every": every,
        "proxy_rotate_on_start": on_start,
        "proxy_rotate_required": required,
        "proxy_list": proxy_list,
        "proxy": base_proxy,
        "proxy_rotate_update_cpa": False,
    }

    # Optional clash knobs only if operator explicitly chose clash.
    if mode == "clash":
        cfg["clash_api"] = _env_first("CLASH_API", "CLASH_CONTROLLER") or None
        cfg["clash_secret"] = _env_first("CLASH_SECRET")
        cfg["clash_proxy_group"] = _env_first("CLASH_GROUP", "CLASH_PROXY_GROUP") or None
        cfg["clash_rule_domains"] = (
            extra.get("clash_rule_domains")
            or _env_first("CLASH_DOMAINS", "CLASH_RULE_DOMAINS")
            or None
        )
        # Drop Nones so proxy_rotate keeps its defaults.
        cfg = {k: v for k, v in cfg.items() if v is not None}

    return cfg


def configure_rotation_once(
    extra: dict[str, Any] | None = None,
    *,
    log_fn: LogFn = None,
    force: bool = False,
) -> dict[str, Any]:
    """Configure process-wide ProxyRotator once (idempotent unless force)."""
    global _CONFIGURED
    from proxy_rotate import configure_proxy_rotation

    cfg = rotation_config_from_env_and_extra(extra)
    if _CONFIGURED and not force:
        return cfg

    def _log(msg: str) -> None:
        if log_fn:
            try:
                log_fn(msg)
            except Exception:
                pass
        else:
            log.info("%s", msg)

    configure_proxy_rotation(cfg, log=_log)
    _CONFIGURED = True
    return cfg


def reset_rotation_for_tests() -> None:
    """Test helper: allow re-configure in the same process."""
    global _CONFIGURED
    _CONFIGURED = False


def resolve_attempt_proxy(
    extra: dict[str, Any] | None = None,
    *,
    log_fn: LogFn = None,
) -> tuple[str, dict[str, Any]]:
    """Rotate (if enabled) and return (proxy_url, rotate_info) for one attempt.

    List mode: returns the concrete pool URL — self-controlled node.
    Clash mode: returns base_proxy (usually local mixed port); node switch is
    side-effect on Clash dedicated group only.
    Off mode: returns explicit/extra/env proxy unchanged.
    """
    from proxy_rotate import current_proxy_override, maybe_rotate_proxy

    cfg = configure_rotation_once(extra, log_fn=log_fn)
    info = maybe_rotate_proxy(log=log_fn, config=cfg)

    override = (current_proxy_override() or "").strip()
    if override:
        return override, info

    # clash / off: use configured base proxy from extra/env
    base = str(
        (extra or {}).get("proxy")
        or cfg.get("proxy")
        or _env_first(
            "CHATGPT_PROXY",
            "MIMO_PROXY",
            "https_proxy",
            "HTTPS_PROXY",
            "http_proxy",
            "HTTP_PROXY",
        )
        or ""
    ).strip()
    return base, info


def inject_attempt_proxy(extra: dict[str, Any] | None = None, *, log_fn: LogFn = None) -> dict[str, Any]:
    """Return a shallow-copied extra dict with ``proxy`` set for this attempt."""
    base = dict(extra or {})
    proxy, info = resolve_attempt_proxy(base, log_fn=log_fn)
    if proxy:
        base["proxy"] = proxy
    if info:
        base["_proxy_rotate"] = {
            k: info.get(k)
            for k in (
                "rotated",
                "mode",
                "label",
                "index",
                "pool_size",
                "group",
                "error",
                "scope",
            )
            if k in info
        }
    return base
