"""Self-check and maintenance ops."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from apps.control_api.settings import get_settings
from apps.control_api.system_ops import cleanup_orphans, selfcheck

router = APIRouter(tags=["ops"])


@router.get("/api/ops/selfcheck")
def api_selfcheck() -> dict:
    root = get_settings().project_root
    return selfcheck(root)


@router.post("/api/ops/cleanup-orphans")
def api_cleanup_orphans(dry_run: bool = Query(default=False)) -> dict:
    return cleanup_orphans(dry_run=dry_run)


# ---------------------------------------------------------------------------
# Outlook auth import/export helpers
#
# Outlook accounts persist as private 0600 per-account JSON files under a
# dedicated ``outlook_auths/`` directory (see OutlookProvider._write_outlook_artifact).
# These helpers expose a strict glob and a redacted public record shape so the
# control API can list/import/export Outlook accounts without ever returning a
# password or refresh_token in a response. They are deliberately separate from
# the ``xai-*.json`` glob in ``accounts_ops``: the two products must not share
# a glob or a directory.
# ---------------------------------------------------------------------------

# A path is a valid Outlook auth file only when its full name starts with
# ``outlook-`` and ends with ``.json``; the collision suffix
# ``outlook-<email>-<ts>-<n>.json`` keeps matching.
def _is_outlook_auth_file(path: Path) -> bool:
    name = path.name
    return name.startswith("outlook-") and name.endswith(".json")


def _outlook_auth_files(root: Path) -> list[Path]:
    """Sorted list of Outlook auth JSON files under ``root`` (no xai overlap)."""
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and _is_outlook_auth_file(p))


_OUTLOOK_REQUIRED_FIELDS = ("email", "password", "client_id", "refresh_token")


def _public_outlook_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Redacted view of one Outlook auth record (never secret values)."""
    missing = [k for k in _OUTLOOK_REQUIRED_FIELDS if not payload.get(k)]
    if missing:
        raise ValueError(f"missing outlook fields: {','.join(sorted(missing))}")
    return {
        "email": str(payload["email"]),
        "client_id": str(payload["client_id"]),
        "bound": bool(payload.get("bound", False)),
        "recovery_email": str(payload.get("recovery_email", "")),
        "has_password": bool(payload.get("password")),
        "has_refresh_token": bool(payload.get("refresh_token")),
    }
