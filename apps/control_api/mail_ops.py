"""Mail pool probe/quarantine ops for the control plane UI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from apps.control_api.config_io import load_config
from mail_pool_probe import (
    QUARANTINABLE_STATUSES,
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
