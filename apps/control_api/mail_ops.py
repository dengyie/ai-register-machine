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
    offset: int | None = None,
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
        offset=offset,
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


# Browser-like UA: bare urllib / Python-urllib/* is blocked by Cloudflare Error 1010
# on temp-mail Workers (edge bot signature). Keep a stable Chrome desktop string.
_CF_DOMAINS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)
# cloudflare_temp_email public settings (no address JWT). ``/api/domains`` is
# address-authed and returns 401 "Invalid address credential" without a JWT.
_CF_OPEN_SETTINGS_PATH = "/open_api/settings"


def _pick_list_payload(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in (
            "results",
            "hydra:member",
            "data",
            "domains",
            "defaultDomains",
            "messages",
        ):
            val = data.get(key)
            if isinstance(val, list):
                return val
        nested = data.get("data")
        if isinstance(nested, dict):
            for key in ("results", "domains", "defaultDomains", "messages"):
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


def _cf_get_json(url: str, headers: dict[str, str], *, timeout: float = 15) -> tuple[int, Any]:
    """GET JSON; raise RuntimeError with short body on HTTP/URL failure."""
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = int(getattr(resp, "status", 200) or 200)
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
    return status, data


def list_cloudflare_domains(root: Path) -> dict[str, Any]:
    """Fetch domain list from the configured Cloudflare temp-mail Worker.

    Uses saved ``cloudflare_api_base`` / ``cloudflare_api_key`` /
    ``cloudflare_auth_mode`` / ``cloudflare_path_domains`` from config (+ .env
    enrich for non-secret base). Never returns the API key.

    Always sends a browser User-Agent (CF Error 1010 otherwise). When the
    configured domains path returns 401/403 (typical for cloudflare_temp_email
    ``/api/domains`` without an address JWT), falls back to public
    ``/open_api/settings`` which exposes ``domains`` / ``defaultDomains``.
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

    headers: dict[str, str] = {
        "Accept": "application/json",
        "User-Agent": _CF_DOMAINS_USER_AGENT,
    }
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

    primary_err: Exception | None = None
    status = 0
    data: Any = []
    used_path = path
    try:
        status, data = _cf_get_json(url, headers)
    except RuntimeError as exc:
        primary_err = exc
        msg = str(exc)
        # 401 address-auth / 403 edge or app deny → public open settings
        if "HTTP 401" not in msg and "HTTP 403" not in msg:
            raise

    domains = _normalize_domain_names(_pick_list_payload(data)) if not primary_err else []
    # Fallback: cloudflare_temp_email public settings (no JWT).
    need_fallback = primary_err is not None or not domains
    if need_fallback and path.rstrip("/") != _CF_OPEN_SETTINGS_PATH.rstrip("/"):
        open_url = f"{api_base}{_CF_OPEN_SETTINGS_PATH}"
        # Public endpoint: drop address/admin auth headers; keep UA + Accept.
        open_headers = {
            "Accept": "application/json",
            "User-Agent": _CF_DOMAINS_USER_AGENT,
        }
        try:
            status, data = _cf_get_json(open_url, open_headers)
            domains = _normalize_domain_names(_pick_list_payload(data))
            used_path = _CF_OPEN_SETTINGS_PATH
            primary_err = None
        except RuntimeError as fallback_exc:
            if primary_err is not None:
                raise primary_err from fallback_exc
            raise

    if primary_err is not None:
        raise primary_err
    if not domains:
        raise RuntimeError(
            f"Cloudflare domains 为空（path={used_path}）。"
            "检查 Worker 是否暴露 /open_api/settings 或配置正确的 domains 路径/凭证"
        )

    selected_raw = str(cfg.get("defaultDomains") or "")
    selected = [x.strip() for x in selected_raw.replace("，", ",").split(",") if x.strip()]
    return {
        "ok": True,
        "api_base": api_base,
        "path": used_path,
        "status": status,
        "count": len(domains),
        "domains": domains,
        "selected": selected,
        "has_api_key": bool(api_key),
    }
