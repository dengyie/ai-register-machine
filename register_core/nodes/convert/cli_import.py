"""CLI helpers for nodes import / validate (kept out of register path)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from register_core.nodes.convert.pipeline import convert_paths, convert_text, pack_result
from register_core.nodes.convert.types import DEFAULT_CONTROLLER, DEFAULT_MIXED_PORT


def run_validate(paths: list[str], *, format_hint: str = "") -> int:
    if not paths:
        text = sys.stdin.read()
        result = convert_text(text, source="stdin", format_hint=format_hint)
    else:
        result = convert_paths([Path(p) for p in paths], format_hint=format_hint)
    out = {
        "ok": result.ok,
        "format": result.format,
        "http_socks": len(result.dialable),
        "protocol": len(result.protocol),
        "needs_core": result.needs_core,
        "types": result.types,
        "errors": result.errors,
        "reports": [r.to_dict() for r in result.reports],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


def run_import(
    paths: list[str],
    *,
    format_hint: str = "",
    nodes_home: Path | None = None,
    nodes_json: Path | None = None,
    mixed_port: int = DEFAULT_MIXED_PORT,
    controller: str = DEFAULT_CONTROLLER,
    max_profile_proxies: int = 400,
    dry_run: bool = False,
    clash_home: Path | None = None,
) -> int:
    path_list = [Path(p) for p in paths]
    # Auto-scan Clash Verge only when user gave no explicit paths (and not pure stdin).
    if not path_list and clash_home and clash_home.is_dir():
        active = clash_home / "clash-verge.yaml"
        if active.is_file():
            path_list.append(active)
        prof = clash_home / "profiles"
        if prof.is_dir():
            path_list.extend(sorted(prof.glob("*.yaml")))

    if not path_list:
        text = sys.stdin.read()
        if not text.strip():
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": "no paths and empty stdin; pass YAML/JSON/URI files",
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
            return 2
        result = convert_text(text, source="stdin", format_hint=format_hint)
        sources: list[Path] = []
    else:
        result = convert_paths(
            path_list, format_hint=format_hint, max_profile_proxies=max_profile_proxies
        )
        sources = [p for p in path_list if p.is_file()]

    if dry_run:
        print(json.dumps(result.to_public_dict(), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1

    if not result.ok:
        print(json.dumps(result.to_public_dict(), ensure_ascii=False, indent=2))
        return 1

    packed = pack_result(
        result,
        nodes_home=nodes_home,
        nodes_json=nodes_json,
        mixed_port=mixed_port,
        controller=controller,
        archive_sources=sources or None,
    )
    public = packed.to_public_dict()
    public["hint"] = (
        "protocol nodes need: python -m register_core nodes core start && "
        "python -m register_core nodes egress set core"
        if packed.needs_core
        else "HTTP/SOCKS only — use egress=list (no mihomo required)"
    )
    print(json.dumps(public, ensure_ascii=False, indent=2))
    return 0 if packed.ok else 1
