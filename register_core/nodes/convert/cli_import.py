"""CLI helpers for nodes import / validate (kept out of register path)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from register_core.nodes.convert.pipeline import (
    convert_paths,
    convert_text,
    load_nodes_for_plan,
    merge_dialable,
    pack_result,
)
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
    replace_nodes: bool = False,
    from_clash_verge: bool = False,
    clash_home: Path | None = None,
    check: bool = False,
    check_timeout: float = 12.0,
) -> int:
    path_list = [Path(p) for p in paths]
    # Clash Verge scan is opt-in only (--from-clash-verge).
    if from_clash_verge and clash_home and clash_home.is_dir():
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
                        "error": (
                            "no paths and empty stdin; pass YAML/JSON/URI files, "
                            "or use --from-clash-verge to scan local Clash Verge profiles"
                        ),
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

    # Always attach merge plan preview (even dry-run / failures with dialable).
    if result.dialable or replace_nodes:
        nodes_path = (nodes_json or Path("nodes.json")).expanduser()
        # resolve relative against repo root when packing; preview uses same default as pack
        from register_core.nodes.convert.pipeline import _ROOT

        if nodes_json is None:
            nodes_path = _ROOT / "nodes.json"
        else:
            nodes_path = Path(nodes_json).expanduser().resolve()
        existing = [] if replace_nodes else load_nodes_for_plan(nodes_path)
        _, plan = merge_dialable(existing, result.dialable, replace=replace_nodes)
        result.merge = plan

    if dry_run:
        public = result.to_public_dict()
        public["dry_run"] = True
        public["hint"] = (
            "dry-run only; re-run without --dry-run to write "
            f"(nodes mode={result.merge.mode if result.merge else 'n/a'})"
        )
        print(json.dumps(public, ensure_ascii=False, indent=2))
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
        replace_nodes=replace_nodes,
    )
    public = packed.to_public_dict()

    # Optional post-import live probe of the HTTP/SOCKS catalog.
    # Import always writes the full catalog; batch register still re-probes and
    # seeds healthy-only rotation (this flag is convenience, not the authority gate).
    # Hybrid profiles (dialable + protocol) still probe the dialable half.
    check_summary: dict | None = None
    dialable_final = int(getattr(packed.merge, "final", 0) or 0) if packed.merge else 0
    should_check = bool(
        check and packed.ok and packed.nodes_path and dialable_final > 0
    )
    if should_check:
        check_summary = _post_import_check(
            nodes_json=Path(packed.nodes_path) if packed.nodes_path else nodes_json,
            timeout=float(check_timeout or 12.0),
        )
        public["check"] = check_summary

    public["hint"] = _import_hint(
        needs_core=bool(packed.needs_core),
        check_summary=check_summary,
        merge_mode=packed.merge.mode if packed.merge else "n/a",
        dialable_final=dialable_final,
    )
    print(json.dumps(public, ensure_ascii=False, indent=2))
    return 0 if packed.ok else 1


def _import_hint(
    *,
    needs_core: bool,
    check_summary: dict | None,
    merge_mode: str,
    dialable_final: int,
) -> str:
    """Operator-facing post-import contract text (batch preflight remains authority)."""
    parts: list[str] = []
    if check_summary is not None:
        healthy = int(check_summary.get("ok") or 0)
        total = int(check_summary.get("total") or 0)
        err = str(check_summary.get("error") or "").strip()
        if err:
            parts.append(
                f"import+check failed: {err}. "
                "Batch register (egress=list|auto) will still re-probe and seed healthy-only rotation."
            )
        else:
            parts.append(
                f"import+check: {healthy}/{total} live. "
                "Batch register (egress=list|auto) will re-probe and rotate only healthy URLs; "
                "dead nodes stay in catalog but never enter the registration pool."
            )
    elif dialable_final > 0:
        parts.append(
            "HTTP/SOCKS catalog written (schema only — not live-probed). "
            "Before each batch register with egress=list|auto, the pipeline probes "
            "nodes.json and seeds healthy-only rotation. Optional now: "
            "`python -m register_core nodes check` or re-import with --check. "
            f"nodes.json mode={merge_mode}"
        )
    if needs_core:
        parts.append(
            "protocol nodes need: python -m register_core nodes core start && "
            "python -m register_core nodes egress set core"
        )
    if not parts:
        parts.append(
            "import finished with no dialable HTTP/SOCKS rows and no protocol runtime. "
            f"nodes.json mode={merge_mode}"
        )
    return " | ".join(parts)


def _post_import_check(
    *,
    nodes_json: Path | None,
    timeout: float = 12.0,
) -> dict:
    """Probe dialable catalog after import; always returns a summary dict."""
    from register_core.nodes.convert.pipeline import _ROOT
    from register_core.nodes.manager import get_manager

    if nodes_json is None:
        path = _ROOT / "nodes.json"
    else:
        path = Path(nodes_json).expanduser().resolve()
    if not path.is_file():
        return {"ok": 0, "total": 0, "path": str(path), "error": f"catalog missing: {path}"}
    # Same-path singleton may still hold pre-pack memory; reload from disk.
    mgr = get_manager(path)
    mgr.reload()
    if not mgr.nodes:
        return {"ok": 0, "total": 0, "path": str(path), "error": "catalog empty"}
    results = mgr.check_all(
        timeout=timeout,
        log=lambda m: print(m, flush=True),
        persist=True,
    )
    ok_n = sum(1 for r in results if r.get("ok"))
    return {
        "ok": ok_n,
        "total": len(results),
        "path": str(path),
        "results": results,
    }
