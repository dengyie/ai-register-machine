#!/usr/bin/env python3
"""Snapshot registered accounts + CPA auth files into project backups/.

Usage (project root):
  uv run python -u scripts/backup_registered_accounts.py
  uv run python -u scripts/backup_registered_accounts.py --no-stamp
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import account_backup  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--no-stamp",
        action="store_true",
        help="Only refresh backups/latest (no register_YYYYMMDD_HHMMSS dir)",
    )
    ap.add_argument("--reason", default="manual")
    args = ap.parse_args()
    res = account_backup.snapshot_registered_accounts(
        _ROOT,
        reason=args.reason,
        make_timestamped=not args.no_stamp,
        log_callback=print,
    )
    print(
        f"OK accounts={res.get('account_count')} cpa={res.get('cpa_count')} "
        f"latest={res.get('latest')} stamped={res.get('stamped') or '-'}"
    )
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
