"""List / inspect / delete product accounts under cpa_auths (disk-first pool)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# Per-process mtime cache so paginated UI polls don't re-parse every auth JSON.
_SUMMARY_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}


def _auth_dir(root: Path) -> Path:
    return root / "cpa_auths"


def _safe_name(name: str) -> str:
    base = Path(str(name or "")).name
    if not base or base in {".", ".."} or "/" in str(name) or "\\" in str(name):
        raise ValueError("invalid account name")
    if not (base.startswith("xai-") and base.endswith(".json")):
        raise ValueError("account file must be xai-*.json")
    return base


def _summarize(path: Path) -> dict[str, Any]:
    st = path.stat()
    mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
    size = int(st.st_size)
    cache_key = str(path)
    hit = _SUMMARY_CACHE.get(cache_key)
    if hit and hit[0] == mtime_ns and hit[1] == size:
        return hit[2]

    email = ""
    complete = False
    has_access = False
    has_refresh = False
    disabled = False
    expired = False
    priority = None
    auth_kind = None
    last_refresh = None
    err = None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            err = "not_object"
        else:
            email = str(data.get("email") or "")
            at = data.get("access_token")
            rt = data.get("refresh_token")
            has_access = isinstance(at, str) and bool(at)
            has_refresh = isinstance(rt, str) and bool(rt)
            complete = has_access and has_refresh
            disabled = bool(data.get("disabled"))
            expired = bool(data.get("expired"))
            priority = data.get("priority")
            auth_kind = data.get("auth_kind") or data.get("type")
            last_refresh = data.get("last_refresh")
    except Exception as exc:
        err = str(exc)[:200]
    summary = {
        "name": path.name,
        "email": email or path.name.removeprefix("xai-").removesuffix(".json"),
        "complete": complete,
        "has_access": has_access,
        "has_refresh": has_refresh,
        "disabled": disabled,
        "expired": expired,
        "priority": priority,
        "auth_kind": auth_kind,
        "last_refresh": last_refresh,
        "size": size,
        "mtime": int(st.st_mtime),
        "mtime_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
        "error": err,
    }
    _SUMMARY_CACHE[cache_key] = (mtime_ns, size, summary)
    return summary


def list_accounts(
    root: Path,
    *,
    q: str = "",
    complete: str = "",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    d = _auth_dir(root)
    if not d.is_dir():
        return {
            "path": str(d),
            "total": 0,
            "complete": 0,
            "page": 1,
            "page_size": page_size,
            "pages": 0,
            "items": [],
        }
    paths = sorted(d.glob("xai-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    items = [_summarize(p) for p in paths]
    # Drop cache entries for files that disappeared (bounded leak guard).
    live = {str(p) for p in paths}
    for k in list(_SUMMARY_CACHE.keys()):
        if k.startswith(str(d)) and k not in live:
            _SUMMARY_CACHE.pop(k, None)

    complete_n = sum(1 for it in items if it.get("complete"))
    qq = (q or "").strip().lower()
    if qq:
        items = [
            it
            for it in items
            if qq in (it.get("email") or "").lower() or qq in (it.get("name") or "").lower()
        ]
    c = (complete or "").strip().lower()
    if c in {"1", "true", "yes", "ok", "complete"}:
        items = [it for it in items if it.get("complete")]
    elif c in {"0", "false", "no", "incomplete"}:
        items = [it for it in items if not it.get("complete")]

    total = len(items)
    if page_size <= 0:
        page = 1
        page_size = total or 1
        page_items = items
        pages = 1 if total else 0
    else:
        page = max(1, int(page or 1))
        page_size = max(1, min(500, int(page_size)))
        pages = (total + page_size - 1) // page_size if total else 0
        if pages and page > pages:
            page = pages
        start = (page - 1) * page_size
        page_items = items[start : start + page_size]
    return {
        "path": str(d),
        "total": total,
        "complete": complete_n if not qq and not c else sum(1 for it in items if it.get("complete")),
        "disk_complete": complete_n,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "items": page_items,
    }


def delete_account(root: Path, name: str) -> dict[str, Any]:
    """Soft-delete: rename into cpa_auths/.trash/ with timestamp suffix (audit/backup)."""
    base = _safe_name(name)
    auth_dir = _auth_dir(root)
    path = auth_dir / base
    if not path.is_file():
        raise FileNotFoundError(base)
    trash = auth_dir / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    dest = trash / f"{base}.{stamp}"
    # Avoid clobber if two deletes land in the same second.
    if dest.exists():
        dest = trash / f"{base}.{stamp}.{int(time.time() * 1000) % 1000}"
    path.rename(dest)
    _SUMMARY_CACHE.pop(str(path), None)
    return {"ok": True, "deleted": base, "archived": dest.name, "archived_path": str(dest)}
