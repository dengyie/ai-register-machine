"""Mail pool probe/quarantine ops for the control plane UI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from apps.control_api.config_io import enrich_config_from_env, load_config
from mail_pool_probe import (
    QUARANTINABLE_STATUSES,
    compact_pool,
    dead_archive_path,
    is_quarantinable,
    pool_stats,
    probe_sample,
    quarantine,
    resolve_pool_path,
)


def _live_path(root: Path) -> Path:
    """Resolve pool path; confine writes under project root (match import_mail).

    Env ``HOTMAIL_ACCOUNTS_FILE`` absolute path outside root is allowed only if
    the file already exists (ops override). Relative paths that escape root are
    forced back to ``root/mail_credentials.txt``.
    """
    root_r = root.resolve()
    cfg = load_config(root)
    cfg_path = str(cfg.get("hotmail_accounts_file") or "").strip() or None
    path = resolve_pool_path(root, config_path=cfg_path)
    try:
        path.resolve().relative_to(root_r)
        return path.resolve()
    except ValueError:
        # Outside root
        if path.is_absolute() and path.is_file():
            # Explicit existing absolute (env) — allow read/probe/quarantine there
            return path.resolve()
        return root_r / "mail_credentials.txt"


def get_pool_stats(root: Path) -> dict[str, Any]:
    live = _live_path(root)
    dead = dead_archive_path(live)
    return pool_stats(live, dead_path=dead)


def probe_mail(
    root: Path,
    *,
    domains: list[str] | None = None,
    limit: int = 30,
    seed: int | None = None,
    concurrency: int = 4,
    wall_seconds: float = 90.0,
) -> dict[str, Any]:
    live = _live_path(root)
    if not live.is_file():
        raise FileNotFoundError(f"mail credentials file not found: {live}")
    return probe_sample(
        live,
        domains=domains,
        limit=limit,
        seed=seed,
        concurrency=concurrency,
        writeback_rotated=True,
        wall_seconds=wall_seconds,
    )


def compact_mail(
    root: Path,
    *,
    dry_run: bool = False,
    drop_invalid: bool = True,
    drop_comments: bool = False,
) -> dict[str, Any]:
    """Dedupe + optional junk strip for the live mail pool (backup first)."""
    live = _live_path(root)
    if not live.is_file():
        raise FileNotFoundError(f"mail credentials file not found: {live}")
    return compact_pool(
        live,
        dry_run=dry_run,
        drop_invalid=drop_invalid,
        drop_comments=drop_comments,
    )


def quarantine_mail(
    root: Path,
    emails: list[str],
    *,
    reason: str = "quarantine",
    require_quarantinable_from: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Quarantine explicit emails.

    If ``require_quarantinable_from`` is provided (last probe rows), only emails
    whose status is in QUARANTINABLE_STATUSES are accepted; others are reported
    in ``skipped_not_quarantinable``. Default (API path) still accepts the
    explicit list — UI only sends quarantinable rows.
    """
    if not emails:
        raise ValueError("emails must be a non-empty list")
    live = _live_path(root)
    if not live.is_file():
        raise FileNotFoundError(f"mail credentials file not found: {live}")

    clean = [e.strip() for e in emails if e and str(e).strip()]
    if not clean:
        raise ValueError("emails must be a non-empty list")

    skipped: list[str] = []
    if require_quarantinable_from is not None:
        allowed = {
            str(r.get("email") or "").strip().lower()
            for r in require_quarantinable_from
            if is_quarantinable(str(r.get("status") or ""))
        }
        kept = []
        for e in clean:
            if e.lower() in allowed:
                kept.append(e)
            else:
                skipped.append(e)
        clean = kept
        if not clean:
            raise ValueError(
                "no quarantinable emails in selection "
                f"(allowed statuses: {sorted(QUARANTINABLE_STATUSES)})"
            )

    out = quarantine(live, clean, reason=reason or "quarantine")
    if skipped:
        out["skipped_not_quarantinable"] = skipped
    return out


def _pick_list_payload(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "hydra:member", "data", "domains", "messages"):
            val = data.get(key)
            if isinstance(val, list):
                return val
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("results", "domains", "messages"):
                val = nested.get(key)
                if isinstance(val, list):
                    return val
    return []


def _one_domain_name(item: Any) -> str:
    if isinstance(item, str):
        name = item.strip()
    elif isinstance(item, dict):
        name = ""
        for key in ("domain", "name", "address"):
            raw = item.get(key)
            if not raw:
                continue
            name = str(raw).strip()
            break
    else:
        name = ""
    if not name:
        return ""
    if "@" in name:
        name = name.rsplit("@", 1)[-1].strip()
    return name


def _normalize_domain_names(items: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = _one_domain_name(item)
        if not name:
            continue
        low = name.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(name)
    return out


def list_cloudflare_domains(root: Path) -> dict[str, Any]:
    """Fetch domain list from the configured Cloudflare temp-mail Worker.

    Uses saved ``cloudflare_api_base`` / ``cloudflare_api_key`` /
    ``cloudflare_auth_mode`` / ``cloudflare_path_domains`` from config (+ .env
    enrich for non-secret base). Never returns the API key.
    """
    cfg = enrich_config_from_env(root, load_config(root))
    api_base = str(cfg.get("cloudflare_api_base") or os.environ.get("CLOUDFLARE_API_BASE") or "").strip().rstrip("/")
    if not api_base:
        raise ValueError("cloudflare_api_base 未配置（资源页 Cloudflare 面板填写后保存）")

    api_key = str(cfg.get("cloudflare_api_key") or os.environ.get("CLOUDFLARE_API_KEY") or "").strip()
    mode = str(
        cfg.get("cloudflare_auth_mode")
        or os.environ.get("CLOUDFLARE_AUTH_MODE")
        or "bearer"
    ).strip().lower() or "bearer"
    path = str(cfg.get("cloudflare_path_domains") or "/domains").strip() or "/domains"
    if not path.startswith("/"):
        path = "/" + path

    headers: dict[str, str] = {"Accept": "application/json"}
    params: dict[str, str] = {}
    if api_key:
        if mode == "x-api-key":
            headers["X-API-Key"] = api_key
        elif mode == "query-key":
            params["key"] = api_key
        elif mode != "none":
            headers["Authorization"] = f"Bearer {api_key}"

    url = f"{api_base}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"

    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = getattr(resp, "status", 200) or 200
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            body = str(exc.reason or exc)
        raise RuntimeError(f"Cloudflare domains HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cloudflare domains 请求失败: {exc.reason}") from exc

    try:
        data = json.loads(raw) if raw else []
    except json.JSONDecodeError as exc:
        raise RuntimeError("Cloudflare domains 返回非 JSON") from exc

    domains = _normalize_domain_names(_pick_list_payload(data))
    selected_raw = str(cfg.get("defaultDomains") or "")
    selected = [x.strip() for x in selected_raw.replace("，", ",").split(",") if x.strip()]
    return {
        "ok": True,
        "api_base": api_base,
        "path": path,
        "status": status,
        "count": len(domains),
        "domains": domains,
        "selected": selected,
        "has_api_key": bool(api_key),
    }
