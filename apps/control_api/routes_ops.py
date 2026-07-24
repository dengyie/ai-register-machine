"""Self-check and maintenance ops."""

from __future__ import annotations

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
