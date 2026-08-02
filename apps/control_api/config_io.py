"""Load / redact / save project config.json with backup-before-write.

Console is the control plane: saving config also mirrors operational keys into
``.env`` so batch supervisors (which load ``.env``) follow the UI.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

SECRET_KEY_SUBSTR = (
    "password",
    "api_key",
    "apikey",
    "token",
    "jwt",
    "secret",
    "credential",
)


def _is_comment_key(key: str) -> bool:
    return key.startswith("//") or key.startswith("#")


def _is_secret_key(key: str) -> bool:
    low = key.lower()
    return any(s in low for s in SECRET_KEY_SUBSTR)


def load_config(root: Path) -> dict[str, Any]:
    path = root / "config.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("config.json must be a JSON object")
    return {k: v for k, v in raw.items() if not _is_comment_key(str(k))}


def redact_config(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in data.items():
        if _is_secret_key(str(k)) and isinstance(v, str) and v:
            if len(v) <= 4:
                out[k] = "***"
            else:
                out[k] = f"***{v[-4:]}"
        else:
            out[k] = v
    return out


def _looks_masked(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("***")


# config.json key → .env / process env key (non-secret operational controls only).
# Secrets (api keys, passwords, tokens) stay out of console→.env sync.
CONFIG_TO_ENV: dict[str, str] = {
    "email_provider": "EMAIL_PROVIDER",
    "email_providers": "EMAIL_PROVIDERS",
    "email_provider_strategy": "EMAIL_PROVIDER_STRATEGY",
    "defaultDomains": "DEFAULT_DOMAINS",
    "cloudflare_api_base": "CLOUDFLARE_API_BASE",
    "cloudflare_auth_mode": "CLOUDFLARE_AUTH_MODE",
    "hotmail_accounts_file": "HOTMAIL_ACCOUNTS_FILE",
    "mail_timeout": "MAIL_TIMEOUT",
    "mail_poll_interval": "MAIL_POLL_INTERVAL",
    "proxy": "PROXY",
    "cpa_proxy": "CPA_PROXY",
    "cpa_probe_chat": "CPA_PROBE_CHAT",
    "cpa_remote_inject": "CPA_REMOTE_INJECT",
    "cpa_export_enabled": "CPA_EXPORT_ENABLED",
    "proxy_rotate_mode": "PROXY_ROTATE_MODE",
    "proxy_rotate_every": "PROXY_ROTATE_EVERY",
    "proxy_list": "PROXY_LIST",
}

ENV_TO_CONFIG: dict[str, str] = {v: k for k, v in CONFIG_TO_ENV.items()}

# Empty string is a real "clear" for multi-select / free-form lists only.
# All other operational keys treat "" as "omit / keep existing" so a partial
# Register save cannot wipe PROXY (etc.) from .env or config.json.
# defaultDomains is clearable so Resources can intentionally empty the domain pool
# (Register never posts this key — omit keeps prior).
CLEARABLE_EMPTY_CONFIG_KEYS = frozenset(
    {
        "email_providers",
        "proxy_list",
        "defaultDomains",
    }
)


_SAFE_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Allowlisted .env keys the control plane may write (sync + start_run inject).
ENV_ALLOWLIST = frozenset(CONFIG_TO_ENV.values()) | frozenset(
    {
        "CPA_BATCH_END_INJECT",
        "CPA_BATCH_IMPORT_EVERY",
        "CPA_BATCH_IMPORT_SIZE",
        "CPA_BATCH_IMPORT_PAUSE",
        "SKIP_CLASH_PREFLIGHT",
        "NODE_SCORE",
        "SUPERVISOR_CHUNK",
        # Outlook live gate — the operator-controlled flag that turns on
        # browser orchestration for the Outlook provider. A *name*, never a
        # secret value; allowlisting lets the control plane pass it through
        # start_run extra_env / .env sync so the gate can be turned on
        # out-of-band without exposing proxy/email/token env vars (those are
        # never allowlisted).
        "GROK_REGISTER_OUTLOOK_LIVE",
    }
)


def _format_env_value(value: Any) -> str | None:
    """Serialize a config value for .env. None = clear/omit key."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        parts = [str(x).strip() for x in value if str(x).strip()]
        return ",".join(parts) if parts else ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value).strip()
    return text


def _is_blank_config_value(value: Any) -> bool:
    """True when a payload value should not overwrite sticky operational state."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, tuple, set)) and not any(str(x).strip() for x in value):
        return True
    return False


def parse_env_file(env_path: Path) -> dict[str, str]:
    """Parse simple KEY=value .env lines (no export/quotes expansion)."""
    out: dict[str, str] = {}
    if not env_path.is_file():
        return out
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or not _SAFE_ENV_KEY.match(key):
            continue
        out[key] = val
    return out


def enrich_config_from_env(root: Path, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fill blank non-secret operational config fields from project ``.env``.

    Response-only helper for GET /api/config so the console shows the values
    the batch actually runs with when config.json still has empty proxy/domains.
    Does not write disk.
    """
    data = dict(cfg if isinstance(cfg, dict) else load_config(root))
    env_map = parse_env_file(root / ".env")
    if not env_map:
        return data
    for env_key, cfg_key in ENV_TO_CONFIG.items():
        if _is_secret_key(cfg_key):
            continue
        if env_key not in env_map:
            continue
        env_val = str(env_map.get(env_key) or "").strip()
        if not env_val:
            continue
        cur = data.get(cfg_key)
        if not _is_blank_config_value(cur):
            continue
        # Bool-ish env for known bool config keys.
        if cfg_key in ("cpa_probe_chat", "cpa_remote_inject", "cpa_export_enabled"):
            low = env_val.lower()
            if low in ("1", "true", "yes", "on"):
                data[cfg_key] = True
            elif low in ("0", "false", "no", "off"):
                data[cfg_key] = False
            else:
                data[cfg_key] = env_val
        elif cfg_key in ("mail_timeout", "mail_poll_interval", "proxy_rotate_every"):
            try:
                data[cfg_key] = int(env_val)
            except ValueError:
                data[cfg_key] = env_val
        elif cfg_key == "email_providers":
            data[cfg_key] = [p.strip() for p in env_val.split(",") if p.strip()]
        elif cfg_key == "proxy_list":
            # PROXY_LIST may be comma or newline separated in env; keep string.
            data[cfg_key] = env_val
        else:
            data[cfg_key] = env_val
    return data


def config_to_env_map(
    cfg: dict[str, Any] | None,
    *,
    only_keys: set[str] | list[str] | None = None,
    skip_empty: bool = False,
) -> dict[str, str]:
    """Map operational config keys → env strings.

    ``only_keys``: if set, only these config.json keys are considered (used on
    save so a hotmail switch does not wipe unrelated PROXY/DEFAULT_DOMAINS).
    ``skip_empty``: omit empty-string values (start_run inject must not clobber
    a healthy process/env PROXY with config proxy=\"\").
    Sticky keys (everything except CLEARABLE_EMPTY_CONFIG_KEYS) never emit an
    empty string even when skip_empty is False — only multi-select/list fields
    may intentionally clear with ``KEY=``.
    """
    out: dict[str, str] = {}
    if not isinstance(cfg, dict):
        return out
    allow_cfg = set(only_keys) if only_keys is not None else None
    for cfg_key, env_key in CONFIG_TO_ENV.items():
        if allow_cfg is not None and cfg_key not in allow_cfg:
            continue
        if env_key not in ENV_ALLOWLIST or not _SAFE_ENV_KEY.match(env_key):
            continue
        if cfg_key not in cfg:
            continue
        if _is_secret_key(cfg_key):
            continue
        formatted = _format_env_value(cfg.get(cfg_key))
        if formatted is None:
            continue
        if formatted == "":
            # Intentional clear only for multi-select / free-form lists.
            if skip_empty or cfg_key not in CLEARABLE_EMPTY_CONFIG_KEYS:
                continue
        out[env_key] = formatted
    return out


def update_env_file(
    env_path: Path,
    updates: dict[str, str],
    *,
    backup: bool = True,
) -> dict[str, Any]:
    """Upsert allowlisted KEY=value lines in ``.env``; preserve comments/unknown keys.

    Empty string values write ``KEY=`` (clears multi-select pools). Keys not in
    ENV_ALLOWLIST are ignored. Returns changed env keys + optional backup path.
    """
    clean: dict[str, str] = {}
    for k, v in (updates or {}).items():
        key = str(k).strip()
        if key not in ENV_ALLOWLIST or not _SAFE_ENV_KEY.match(key):
            continue
        clean[key] = str(v)

    if not clean:
        return {"changed_env_keys": [], "backup": None, "path": str(env_path)}

    existing_text = ""
    if env_path.is_file():
        existing_text = env_path.read_text(encoding="utf-8")

    lines = existing_text.splitlines(keepends=True) if existing_text else []
    # Normalize to list of lines with newline for rewrite.
    if lines and not lines[-1].endswith("\n"):
        lines[-1] = lines[-1] + "\n"

    seen: set[str] = set()
    changed: list[str] = []
    new_lines: list[str] = []
    for line in lines:
        raw = line.rstrip("\n")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line if line.endswith("\n") else line + "\n")
            continue
        key, _, old_val = stripped.partition("=")
        key = key.strip()
        if key not in clean:
            new_lines.append(line if line.endswith("\n") else line + "\n")
            continue
        seen.add(key)
        new_val = clean[key]
        if old_val != new_val:
            changed.append(key)
        new_lines.append(f"{key}={new_val}\n")

    for key, new_val in clean.items():
        if key in seen:
            continue
        changed.append(key)
        new_lines.append(f"{key}={new_val}\n")

    bak_path: str | None = None
    if changed:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        if backup and env_path.is_file():
            ts = time.strftime("%Y%m%d_%H%M%S")
            bak = env_path.with_name(f".env.bak-web-{ts}")
            shutil.copy2(env_path, bak)
            bak_path = str(bak)
        tmp = env_path.with_name(".env.tmp-web")
        tmp.write_text("".join(new_lines), encoding="utf-8")
        tmp.replace(env_path)

    return {
        "changed_env_keys": changed,
        "backup": bak_path,
        "path": str(env_path),
    }


def sync_config_to_env(
    root: Path,
    cfg: dict[str, Any] | None = None,
    *,
    only_keys: set[str] | list[str] | None = None,
    skip_empty: bool = False,
) -> dict[str, Any]:
    """Mirror operational config fields into project ``.env`` (console control plane)."""
    data = cfg if isinstance(cfg, dict) else load_config(root)
    return update_env_file(
        root / ".env",
        config_to_env_map(data, only_keys=only_keys, skip_empty=skip_empty),
    )


def save_config(root: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Write config.json and mirror **changed** operational keys into ``.env``.

    Empty or masked secret fields keep previous values. Only keys present in
    the put payload that are operational (CONFIG_TO_ENV) are written to .env,
    so saving email_provider does not blank PROXY/DEFAULT_DOMAINS.

    Sticky empty: for operational keys outside CLEARABLE_EMPTY_CONFIG_KEYS,
    a blank payload value is ignored for both config.json merge and .env sync
    (keeps prior non-empty). Multi-select fields like email_providers may still
    clear with "".
    """
    path = root / "config.json"
    existing = load_config(root) if path.is_file() else {}
    merged: dict[str, Any] = dict(existing)

    changed: list[str] = []
    payload_keys: list[str] = []
    for key, value in data.items():
        if _is_comment_key(str(key)):
            continue
        if _is_secret_key(str(key)):
            if value in ("", None) or _looks_masked(value):
                continue
        key_s = str(key)
        # Sticky operational fields: blank payload must not wipe prior value.
        if (
            key_s in CONFIG_TO_ENV
            and key_s not in CLEARABLE_EMPTY_CONFIG_KEYS
            and _is_blank_config_value(value)
        ):
            continue
        payload_keys.append(key_s)
        old = existing.get(key)
        if old != value:
            changed.append(key_s)
        merged[key] = value

    backup: str | None = None
    if path.is_file():
        ts = time.strftime("%Y%m%d_%H%M%S")
        bak = path.with_name(f"config.json.bak-web-{ts}")
        shutil.copy2(path, bak)
        backup = str(bak)

    tmp = path.with_suffix(".json.tmp-web")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

    # Sync only operational keys that were part of this save payload.
    # config_to_env_map also refuses sticky empty → double protection if caller
    # ever passes merged-with-blank.
    env_keys = [k for k in payload_keys if k in CONFIG_TO_ENV]
    env_sync: dict[str, Any] = {"changed_env_keys": [], "backup": None}
    if env_keys:
        env_sync = sync_config_to_env(root, merged, only_keys=env_keys)
    return {
        "backup": backup,
        "changed_keys": changed,
        "changed_env_keys": list(env_sync.get("changed_env_keys") or []),
        "env_backup": env_sync.get("backup"),
        "config": redact_config(merged),
    }
