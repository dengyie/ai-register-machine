#!/usr/bin/env python3
"""CLI: sample-probe Hotmail/Outlook mail credentials (OAuth refresh only).

Usage:
  python scripts/probe_mail_pool.py --domains hotmail.com,outlook.com --limit 30
  python scripts/probe_mail_pool.py --limit 10 --seed 20260724 --json
  python scripts/probe_mail_pool.py --limit 20 --quarantine-dead

Env:
  HOTMAIL_ACCOUNTS_FILE  override pool path
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from mail_pool_probe import (  # noqa: E402
    KNOWN_DOMAINS,
    QUARANTINABLE_STATUSES,
    STATUS_OK,
    compact_pool,
    dead_archive_path,
    is_quarantinable,
    pool_stats,
    probe_sample,
    quarantine,
    resolve_pool_path,
)


def _parse_domains(raw: str | None) -> list[str] | None:
    if not raw or not str(raw).strip():
        return None
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    return parts or None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Probe mail credential pool (OAuth refresh)")
    ap.add_argument(
        "--file",
        default="",
        help="pool file path (default: HOTMAIL_ACCOUNTS_FILE / config / mail_credentials.txt)",
    )
    ap.add_argument(
        "--domains",
        default="",
        help=f"comma-separated domains (known: {','.join(KNOWN_DOMAINS)}); empty=all",
    )
    ap.add_argument("--limit", type=int, default=30, help="sample size (1-200, default 30)")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for reproducible sample")
    ap.add_argument("--concurrency", type=int, default=4, help="workers (1-8, default 4)")
    ap.add_argument(
        "--wall-seconds",
        type=float,
        default=90.0,
        help="max wall-clock seconds for the probe wave (default 90)",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable JSON output")
    ap.add_argument(
        "--quarantine-dead",
        action="store_true",
        help=(
            "after probe, archive quarantinable failures from THIS run only "
            f"(statuses: {', '.join(sorted(QUARANTINABLE_STATUSES))}; "
            "skips network_error/unknown)"
        ),
    )
    ap.add_argument(
        "--no-writeback",
        action="store_true",
        help="do not write rotated refresh_token back to the pool",
    )
    ap.add_argument(
        "--stats-only",
        action="store_true",
        help="print pool stats and exit (no network)",
    )
    ap.add_argument(
        "--compact",
        action="store_true",
        help="dedupe live pool by email (first wins); backup first",
    )
    ap.add_argument(
        "--compact-dry-run",
        action="store_true",
        help="preview compact without rewriting the pool",
    )
    ap.add_argument(
        "--drop-comments",
        action="store_true",
        help="with --compact, also strip # comments and blank lines",
    )
    args = ap.parse_args(argv)

    if args.file:
        live = Path(args.file).expanduser()
        if not live.is_absolute():
            live = (_REPO / live).resolve()
    else:
        live = resolve_pool_path(_REPO)

    if not live.is_file():
        print(f"ERROR: pool file not found: {live}", file=sys.stderr)
        return 2

    if args.stats_only:
        st = pool_stats(live)
        if args.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
        else:
            print(f"path: {st['path']}")
            print(f"total: {st['total']}")
            print(
                f"raw_lines: {st.get('raw_lines')}  "
                f"duplicate_extra: {st.get('duplicate_extra')}  "
                f"invalid: {st.get('invalid_lines')}  "
                f"needs_compact: {st.get('needs_compact')}"
            )
            for dom, n in (st.get("by_domain") or {}).items():
                print(f"  {dom}: {n}")
            print(f"dead_path: {st['dead_path']} ({st['dead_total']})")
        return 0

    if args.compact or args.compact_dry_run:
        # --compact-dry-run alone (or with --compact) previews; --compact alone writes.
        dry = bool(args.compact_dry_run) or not bool(args.compact)
        if args.compact and not args.compact_dry_run:
            dry = False
        out = compact_pool(
            live,
            dry_run=dry,
            drop_invalid=True,
            drop_comments=bool(args.drop_comments),
        )
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(out.get("summary") or out)
            print(
                f"unique={out.get('unique')} dup_extra={out.get('duplicate_extra')} "
                f"invalid_dropped={out.get('invalid_dropped')} "
                f"backup={out.get('backup_path')}"
            )
        return 0

    domains = _parse_domains(args.domains)
    limit = max(1, min(int(args.limit), 200))
    concurrency = max(1, min(int(args.concurrency), 8))

    try:
        out = probe_sample(
            live,
            domains=domains,
            limit=limit,
            seed=args.seed,
            concurrency=concurrency,
            writeback_rotated=not args.no_writeback,
            wall_seconds=float(args.wall_seconds),
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3

    if args.quarantine_dead:
        dead_emails = [
            r["email"]
            for r in out.get("results") or []
            if is_quarantinable(str(r.get("status") or "")) and r.get("email")
        ]
        skipped = [
            r["email"]
            for r in out.get("results") or []
            if r.get("status") != STATUS_OK
            and not is_quarantinable(str(r.get("status") or ""))
            and r.get("email")
        ]
        if dead_emails:
            q = quarantine(
                live,
                dead_emails,
                reason="probe:cli",
                dead_path=dead_archive_path(live),
            )
            q["skipped_soft_fail"] = skipped
            out["quarantine"] = q
        else:
            out["quarantine"] = {
                "removed": 0,
                "not_found": 0,
                "skipped_soft_fail": skipped,
            }

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"pool: {live}")
    if domains:
        print(f"domains: {', '.join(domains)}")
    print(
        f"probed={out['probed']} ok={out['ok']} dead={out['dead']} "
        f"quarantinable={out.get('quarantinable', 0)}"
        + (" TIMED_OUT" if out.get("timed_out") else "")
    )
    by = out.get("by_status") or {}
    if by:
        print("by_status: " + ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
    print("-" * 72)
    print(f"{'label':<8} {'status':<16} {'q':<3} {'domain':<14} email  reason")
    for r in out.get("results") or []:
        st = r.get("status") or ""
        if st == STATUS_OK:
            label = "好用"
        elif is_quarantinable(st):
            label = "挂了"
        else:
            label = "不定"
        qflag = "Y" if r.get("quarantinable") else "-"
        reason = (r.get("ms_error") or r.get("reason") or "")[:60]
        print(
            f"{label:<8} {st:<16} {qflag:<3} {r.get('domain', ''):<14} "
            f"{r.get('email', '')}  {reason}"
        )
    if "quarantine" in out:
        q = out["quarantine"]
        print("-" * 72)
        print(
            f"quarantine: removed={q.get('removed')} "
            f"not_found={q.get('not_found')} "
            f"skipped_soft={len(q.get('skipped_soft_fail') or [])} "
            f"backup={q.get('backup_path', '')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
