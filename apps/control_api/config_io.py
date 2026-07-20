"""Load / redact / save project config.json with backup-before-write."""

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


def save_config(root: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Write config.json. Empty or masked secret fields keep previous values."""
    path = root / "config.json"
    existing = load_config(root) if path.is_file() else {}
    merged: dict[str, Any] = dict(existing)

    changed: list[str] = []
    for key, value in data.items():
        if _is_comment_key(str(key)):
            continue
        if _is_secret_key(str(key)):
            if value in ("", None) or _looks_masked(value):
                continue
        old = existing.get(key)
        if old != value:
            changed.append(str(key))
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
    return {
        "backup": backup,
        "changed_keys": changed,
        "config": redact_config(merged),
    }


_SAFE_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Allowlisted .env keys for optional future use (not free-form dump).
ENV_ALLOWLIST = frozenset(
    {
        "DEFAULT_DOMAINS",
        "EMAIL_PROVIDER",
        "EMAIL_PROVIDERS",
        "CPA_PROBE_CHAT",
        "CPA_REMOTE_INJECT",
        "CPA_BATCH_END_INJECT",
        "CPA_EXPORT_ENABLED",
        "PROXY_LIST",
        "SKIP_CLASH_PREFLIGHT",
        "NODE_SCORE",
        "SUPERVISOR_CHUNK",
    }
)
