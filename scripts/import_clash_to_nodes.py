#!/usr/bin/env python3
"""Thin wrapper: import Clash/V2Ray/URI into project nodes (compat entry).

Prefer:
  python -m register_core nodes import path/to/profile.yaml
  python -m register_core nodes validate path/to/profile.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from register_core.nodes.convert.cli_import import run_import  # noqa: E402
from register_core.nodes.convert.types import DEFAULT_CONTROLLER, DEFAULT_MIXED_PORT  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Import Clash YAML / V2Ray JSON / URI into .nodes + nodes.json"
    )
    ap.add_argument("paths", nargs="*", type=Path, help="YAML/JSON/txt files or dirs")
    ap.add_argument(
        "--clash-home",
        type=Path,
        default=Path.home()
        / "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev",
        help="Clash Verge data dir (mac default)",
    )
    ap.add_argument("--no-clash-home", action="store_true")
    ap.add_argument("--max-profile-proxies", type=int, default=400)
    ap.add_argument("--mixed-port", type=int, default=DEFAULT_MIXED_PORT)
    ap.add_argument("--controller", default=DEFAULT_CONTROLLER)
    ap.add_argument("--nodes-home", type=Path, default=_ROOT / ".nodes")
    ap.add_argument("--nodes-json", type=Path, default=_ROOT / "nodes.json")
    ap.add_argument("--format", default="", help="clash_yaml|v2ray_json|uri_list")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    return run_import(
        [str(p) for p in (args.paths or [])],
        format_hint=args.format,
        nodes_home=args.nodes_home,
        nodes_json=args.nodes_json,
        mixed_port=int(args.mixed_port),
        controller=str(args.controller),
        max_profile_proxies=int(args.max_profile_proxies),
        dry_run=bool(args.dry_run),
        clash_home=None if args.no_clash_home else args.clash_home,
    )


if __name__ == "__main__":
    raise SystemExit(main())
