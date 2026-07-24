"""Overview aggregates for the control plane."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def count_product_ok(root: Path) -> int:
    """Count xai-*.json under cpa_auths with both access_token and refresh_token."""
    d = root / "cpa_auths"
    if not d.is_dir():
        return 0
    n = 0
    for f in d.glob("xai-*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        at = data.get("access_token")
        rt = data.get("refresh_token")
        if isinstance(at, str) and at and isinstance(rt, str) and rt:
            n += 1
    return n


def build_overview(root: Path, run_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    run = run_summary
    if run is None:
        try:
            from apps.control_api.runs import run_status

            run = run_status(root)
        except Exception:
            run = None
    nodes_summary: dict[str, Any] | None = None
    try:
        from apps.control_api.nodes_ops import list_catalog

        cat = list_catalog(root)
        nodes_summary = {
            "total": cat.get("total"),
            "enabled": cat.get("enabled"),
            "healthy": cat.get("healthy"),
            "path": cat.get("path"),
        }
    except Exception:
        nodes_summary = None
    return {
        "project_root": str(root),
        "product_ok": count_product_ok(root),
        "run": run,
        "nodes": nodes_summary,
    }
