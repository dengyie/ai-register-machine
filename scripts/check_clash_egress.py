#!/usr/bin/env python3
"""Authoritative Clash/proxy egress IP check (ops).

Prefer this over bare ``curl -x http://127.0.0.1:7897 https://api.ipify.org``
on Bohrium/pxed: system curl can report host CN IP (e.g. 39.98.70.173) even when
mihomo leaf is overseas and delay API is green. Uses curl_cffi when available
(same stack as ChatGPT provider / node L1 probes), else urllib.

Usage:
  .venv/bin/python scripts/check_clash_egress.py
  .venv/bin/python scripts/check_clash_egress.py --proxy http://127.0.0.1:7897
  .venv/bin/python scripts/check_clash_egress.py --json
  .venv/bin/python scripts/check_clash_egress.py --expect-not 39.98.70.173

Exit: 0 if ok (+ expect filters pass), 2 if probe failed / expect mismatch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from register_core.nodes.health import probe_egress_ip  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Probe public egress IP via HTTP proxy")
    p.add_argument(
        "--proxy",
        default="http://127.0.0.1:7897",
        help="HTTP proxy URL (default Clash mixed-port)",
    )
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument("--json", action="store_true", help="print full result JSON")
    p.add_argument(
        "--expect-not",
        action="append",
        default=[],
        metavar="IP",
        help="fail if egress equals this IP (repeatable); use for known host CN",
    )
    p.add_argument(
        "--expect",
        default="",
        metavar="IP",
        help="optional exact IP match requirement",
    )
    args = p.parse_args(argv)

    result = probe_egress_ip(args.proxy, timeout=args.timeout)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result.get("ok"):
            print(
                f"EGRESS_IP={result['ip']} backend={result.get('backend')} "
                f"ms={result.get('ms')} proxy={args.proxy} url={result.get('url')}"
            )
        else:
            print(
                f"EGRESS_IP=FAIL error={result.get('error')} "
                f"backend={result.get('backend')} proxy={args.proxy}",
                file=sys.stderr,
            )

    if not result.get("ok"):
        return 2

    ip = str(result.get("ip") or "")
    if args.expect and ip != args.expect.strip():
        print(f"EXPECT_MISMATCH want={args.expect!r} got={ip!r}", file=sys.stderr)
        return 2
    banned = {x.strip() for x in (args.expect_not or []) if str(x).strip()}
    if ip in banned:
        print(
            f"EXPECT_NOT hit banned host/CN IP={ip!r} "
            f"(proxy may be bypassed or leaf REJECT; do not trust bare curl either)",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
