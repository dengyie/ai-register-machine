#!/usr/bin/env python3
"""Probe mihomo leaf proxies via controller delay API; filter dead from GROK groups.

Production authority for **Grok on Clash mixed-port** (pxed default):
  bash preflight-clash-nodes.sh  → this script --apply-config → restart mihomo

This is **not** the monorepo ``nodes.json`` / ``preflight_nodes_for_register`` path
(list|auto). Both strip dead leaves before batch work; backends differ.

Usage:
  python3 scripts/probe_clash_nodes.py                 # probe + select
  python3 scripts/probe_clash_nodes.py --dry-run       # probe only
  python3 scripts/probe_clash_nodes.py --apply-config  # rewrite config groups
  python3 scripts/probe_clash_nodes.py --rounds 3 --purge-proxies --apply-config
      # 3× delay probe; only leaves that fail ALL rounds are dead;
      # --purge-proxies also drops them from proxies: (not just group lists)

Env:
  CLASH_DIR          default /personal/clash
  CLASH_API          default http://127.0.0.1:9090
  CLASH_REPORT_DIR   default <repo>/output
  CLASH_CONFIG       override config.yaml path
  CLASH_MERGED       override config.mac-merged.yaml path
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]

CLASH_DIR = Path(os.environ.get("CLASH_DIR") or "/personal/clash")
CFG = Path(os.environ.get("CLASH_CONFIG") or (CLASH_DIR / "config.yaml"))
MERGED = Path(
    os.environ.get("CLASH_MERGED") or (CLASH_DIR / "config.mac-merged.yaml")
)
SECRET_FILE = CLASH_DIR / ".controller-secret"
API = (os.environ.get("CLASH_API") or "http://127.0.0.1:9090").rstrip("/")
REPORT_DIR = Path(
    os.environ.get("CLASH_REPORT_DIR") or (_REPO / "output")
)
DEFAULT_URL = "http://www.gstatic.com/generate_204"
DEFAULT_TIMEOUT_MS = 5000
# 每族 .pre-health-* 备份保留份数。不清理会无界增长: 每轮写 2 份,
# 曾累积 1300+ 份 / 343MB 把 /personal/clash 撑到 579MB。
_BAK_RETAIN = int(os.environ.get("CLASH_BAK_RETAIN") or 20)
GROUP_TYPES = {
    "Selector",
    "URLTest",
    "Fallback",
    "LoadBalance",
    "Relay",
    "Direct",
    "Reject",
    "Compatible",
    "Pass",
    "PassRule",
    "RejectDrop",
}
# Groups whose leaf lists we rewrite to healthy-only for register batches
REGISTER_GROUPS = ("🎯Grok注册", "♻️Grok优选", "PROXY", "🔰ChatGPT")
# Dog VPS (35.212.179.13 / mango9502) + hairpin dialer leaves — traffic-precious,
# never front-load into Grok register rotate pool. Prefer residential 家宽 instead.
REGISTER_POOL_EXCLUDE = (
    "GVPS-AnyTLS-googlevps",
    "GVPS-TUIC-googlevps",
)
REGISTER_POOL_EXCLUDE_RE = re.compile(
    r"GVPS-AnyTLS-googlevps|GVPS-TUIC-googlevps|googlevps|P1024-"
)
# Prefer these markers (substring) at front of register groups when healthy
PREFERRED_MARKERS = ("家宽",)
# Legacy explicit names (still preferred if healthy and not excluded)
PREFERRED = (
    "GVPS-VLESS-CF-LSJ",
    "GVPS-VLESS-CF-DLD",
)


def load_secret() -> str:
    """Load controller secret from config.yaml or CLASH_DIR/.controller-secret.

    Never embed a default secret in the repo — missing secret is a hard error.
    """
    if CFG.is_file():
        text = CFG.read_text(encoding="utf-8")
        m = re.search(r'^secret:\s*["\']?([^"\'\n]+)', text, re.M)
        if m:
            secret = m.group(1).strip()
            if secret:
                return secret
    if SECRET_FILE.is_file():
        secret = SECRET_FILE.read_text(encoding="utf-8").strip()
        if secret:
            return secret
    raise SystemExit(
        f"ERROR: mihomo controller secret not found "
        f"(set secret: in {CFG} or write {SECRET_FILE})"
    )


def api_request(
    path: str, secret: str, method: str = "GET", data: bytes | None = None
) -> Any:
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = resp.read()
        if not body:
            return None
        return json.loads(body.decode())


def list_leaves(secret: str) -> list[str]:
    data = api_request("/proxies", secret)
    prox = data.get("proxies") or {}
    leaves: list[str] = []
    for name, info in prox.items():
        t = info.get("type") or ""
        if t in GROUP_TYPES:
            continue
        if name in ("DIRECT", "REJECT", "PASS", "COMPATIBLE"):
            continue
        leaves.append(name)
    return sorted(leaves)


def probe_one(
    name: str, secret: str, timeout_ms: int, url: str
) -> tuple[str, int | None, str]:
    q = urllib.parse.urlencode({"timeout": timeout_ms, "url": url})
    path = f"/proxies/{urllib.parse.quote(name, safe='')}/delay?{q}"
    try:
        data = api_request(path, secret)
        delay = data.get("delay") if isinstance(data, dict) else None
        if isinstance(delay, int) and delay > 0:
            return name, delay, "ok"
        return name, None, f"no_delay:{data!r}"
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode(errors="replace")
        except Exception:
            body = str(e)
        return name, None, f"http_{e.code}:{body[:120]}"
    except Exception as e:
        return name, None, f"{type(e).__name__}:{e}"


def probe_all(
    names: list[str],
    secret: str,
    timeout_ms: int,
    url: str,
    workers: int,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    total = len(names)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(probe_one, n, secret, timeout_ms, url): n for n in names
        }
        for fut in concurrent.futures.as_completed(futs):
            name, delay, status = fut.result()
            results[name] = {
                "delay_ms": delay,
                "status": status,
                "ok": delay is not None,
            }
            done += 1
            if done % 10 == 0 or done == total:
                ok_n = sum(1 for v in results.values() if v["ok"])
                print(f"  progress {done}/{total} ok={ok_n}", flush=True)
    return results


def classify(name: str) -> str:
    if name.startswith("IK-"):
        return "ikuuu"
    if name.startswith("BK-"):
        return "pokemon"
    if name.startswith("GVPS-"):
        return "gvps"
    return "other"


def put_group_now(secret: str, group: str, node: str) -> None:
    path = f"/proxies/{urllib.parse.quote(group, safe='')}"
    body = json.dumps({"name": node}).encode()
    try:
        api_request(path, secret, method="PUT", data=body)
        print(f"  set {group} -> {node}")
    except Exception as e:
        print(f"  WARN set {group}: {e}")


def _leaf_ok(r: dict[str, Any], max_latency_ms: int) -> bool:
    d = r.get("delay_ms")
    return bool(r.get("ok")) and isinstance(d, int) and d > 0 and d <= max_latency_ms


def _require_yaml():
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "PyYAML required for --apply-config / --purge-proxies "
            "(use repo .venv: .venv/bin/python scripts/probe_clash_nodes.py …)"
        ) from exc
    return yaml


def _backup_and_dump(cfg_path: Path, text: str, data: dict, header_note: str) -> Path:
    yaml = _require_yaml()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bak = cfg_path.with_suffix(cfg_path.suffix + f".pre-health-{ts}")
    bak.write_text(text, encoding="utf-8")
    # 每轮 --apply-config 写 2 份备份 (config.yaml + merged), 不清理会无界增长:
    # 曾在 /personal/clash 累积 1300+ 份 / 343MB。只删同族(.pre-health-)最旧的。
    try:
        fam = sorted(cfg_path.parent.glob(cfg_path.name + ".pre-health-*"))
        for stale in fam[:-_BAK_RETAIN]:
            stale.unlink()
    except OSError:
        pass

    class NoAliasDumper(yaml.SafeDumper):
        def ignore_aliases(self, data):  # type: ignore[no-untyped-def]
            return True

    header = ""
    if text.lstrip().startswith("#"):
        lines = text.splitlines(keepends=True)
        buf: list[str] = []
        for ln in lines:
            if ln.startswith("#") or ln.strip() == "":
                buf.append(ln)
                if len(buf) > 20:
                    break
            else:
                break
        header = "".join(buf)
        if header and not header.endswith("\n"):
            header += "\n"
        header += f"# {header_note}\n"

    body = yaml.dump(
        data,
        Dumper=NoAliasDumper,
        allow_unicode=True,
        sort_keys=False,
        width=120,
        default_flow_style=False,
    )
    cfg_path.write_text(header + body, encoding="utf-8")
    print(f"  wrote {cfg_path} (bak {bak.name})")
    return bak


def rewrite_config_groups(
    cfg_path: Path,
    healthy: list[str],
    preferred_first: list[str],
) -> Path:
    """Rewrite proxy-groups leaf lists for register-related groups; backup first."""
    yaml = _require_yaml()
    text = cfg_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    ordered = list(preferred_first) + [
        n for n in healthy if n not in preferred_first
    ]

    groups = data.get("proxy-groups") or []
    healthy_set = set(healthy)
    for g in groups:
        name = g.get("name")
        if name not in REGISTER_GROUPS:
            continue
        old = list(g.get("proxies") or [])
        if g.get("type") == "url-test":
            g["proxies"] = ordered[:] if ordered else old
        else:
            # select groups keep their curated member list (e.g. the 17-node
            # per-exit-IP trim) — strip only leaves dead in every round and
            # preserve order, so rotation still hits distinct exit IPs. Fall
            # back to the full healthy pool only when nothing survives.
            new = [m for m in old if m in healthy_set]
            if not new:
                new = ordered[:]
                for m in ("♻️Grok优选", "DIRECT"):
                    if m not in new and m in old:
                        new.append(m)
            g["proxies"] = new if new else ["DIRECT"]
        print(f"  group {name}: {len(old)} -> {len(g['proxies'])}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return _backup_and_dump(
        cfg_path,
        text,
        data,
        f"health-filter {ts} healthy={len(healthy)} "
        f"dead_stripped_from {','.join(REGISTER_GROUPS)}",
    )


def purge_dead_proxies(
    cfg_path: Path,
    dead: list[str],
) -> Path:
    """Delete all-fail leaves from proxies: and every group list. Backup first.

    Safer than only rewriting REGISTER_GROUPS: dead leaves cannot re-enter via
    other selectors after mihomo reload.
    """
    yaml = _require_yaml()
    dead_set = {n for n in dead if n}
    if not dead_set:
        print(f"  purge {cfg_path}: nothing to delete")
        return cfg_path

    text = cfg_path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    proxies = data.get("proxies") or []
    before = len(proxies)
    kept = []
    removed_names: list[str] = []
    for p in proxies:
        if not isinstance(p, dict):
            kept.append(p)
            continue
        name = str(p.get("name") or "")
        if name in dead_set:
            removed_names.append(name)
            continue
        kept.append(p)
    data["proxies"] = kept

    stripped_groups = 0
    for g in data.get("proxy-groups") or []:
        old = list(g.get("proxies") or [])
        new = [n for n in old if n not in dead_set]
        if len(new) != len(old):
            stripped_groups += 1
            g["proxies"] = new if new else ["DIRECT"]

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    print(
        f"  purge {cfg_path.name}: proxies {before}->{len(kept)} "
        f"removed={len(removed_names)} groups_touched={stripped_groups}"
    )
    if removed_names[:12]:
        for n in removed_names[:12]:
            print(f"    - {n}")
        if len(removed_names) > 12:
            print(f"    … +{len(removed_names) - 12} more")
    return _backup_and_dump(
        cfg_path,
        text,
        data,
        f"purge-dead {ts} removed={len(removed_names)} "
        f"proxies {before}->{len(kept)}",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument(
        "--rounds",
        type=int,
        default=1,
        help="probe each leaf this many times; dead = failed ALL rounds "
        "(default 1; use 3 to only purge stable dead)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="probe only, no group select / config write",
    )
    ap.add_argument(
        "--apply-config",
        action="store_true",
        help="rewrite config.yaml + merged yaml groups to healthy leaves",
    )
    ap.add_argument(
        "--purge-proxies",
        action="store_true",
        help="delete leaves that failed ALL rounds from proxies: + all groups "
        "(stronger than group rewrite alone)",
    )
    ap.add_argument(
        "--select",
        default="",
        help="force group selection to this node if healthy",
    )
    ap.add_argument(
        "--max-latency-ms",
        type=int,
        default=4500,
        help="treat delay above this as dead",
    )
    args = ap.parse_args()
    rounds = max(1, int(args.rounds or 1))

    secret = load_secret()
    print(
        f"clash_dir={CLASH_DIR} api={API} secret_len={len(secret)} "
        f"url={args.url} timeout_ms={args.timeout_ms} rounds={rounds}"
    )
    leaves = list_leaves(secret)
    print(f"leaves={len(leaves)}")

    t0 = time.time()
    round_results: list[dict[str, dict[str, Any]]] = []
    for i in range(rounds):
        print(f"\n=== probe round {i + 1}/{rounds} ===", flush=True)
        rr = probe_all(leaves, secret, args.timeout_ms, args.url, args.workers)
        round_results.append(rr)
        ok_n = sum(
            1 for n in leaves if _leaf_ok(rr.get(n) or {}, args.max_latency_ms)
        )
        print(f"  round {i + 1} ok={ok_n}/{len(leaves)}", flush=True)
    elapsed = time.time() - t0

    # Aggregate: dead only if failed every round (3次不通才删).
    healthy: list[str] = []
    dead: list[str] = []
    results: dict[str, dict[str, Any]] = {}
    fail_counts: dict[str, int] = {}
    for name in leaves:
        fails = 0
        best_delay: int | None = None
        last_status = ""
        for rr in round_results:
            r = rr.get(name) or {"ok": False, "delay_ms": None, "status": "missing"}
            last_status = str(r.get("status") or last_status)
            if _leaf_ok(r, args.max_latency_ms):
                d = int(r["delay_ms"])
                if best_delay is None or d < best_delay:
                    best_delay = d
            else:
                fails += 1
        fail_counts[name] = fails
        if fails >= rounds:
            dead.append(name)
            results[name] = {
                "delay_ms": None,
                "status": f"fail_{fails}/{rounds}:{last_status}",
                "ok": False,
                "fail_rounds": fails,
            }
        else:
            healthy.append(name)
            results[name] = {
                "delay_ms": best_delay,
                "status": f"ok_best fail_{fails}/{rounds}",
                "ok": True,
                "fail_rounds": fails,
            }

    healthy.sort(key=lambda n: results[n]["delay_ms"] or 99999)
    # Register pool: drop dog VPS / dialer-hairpin leaves even if delay-healthy.
    healthy_reg = [
        n
        for n in healthy
        if n not in REGISTER_POOL_EXCLUDE and not REGISTER_POOL_EXCLUDE_RE.search(n)
    ]
    preferred_residential = [
        n
        for n in healthy_reg
        if any(m in n for m in PREFERRED_MARKERS)
    ]
    preferred_named = [n for n in PREFERRED if n in set(healthy_reg)]
    preferred_first = preferred_residential + [
        n for n in preferred_named if n not in preferred_residential
    ]

    by_cls_ok = Counter(classify(n) for n in healthy)
    by_cls_dead = Counter(classify(n) for n in dead)

    print("\n=== SUMMARY ===")
    print(f"elapsed_s={elapsed:.1f} rounds={rounds}")
    print(f"healthy={len(healthy)} dead_all_rounds={len(dead)}")
    print(f"healthy_by_class={dict(by_cls_ok)}")
    print(f"dead_by_class={dict(by_cls_dead)}")
    print("\n--- HEALTHY (best delay ms) ---")
    for n in healthy:
        print(
            f"  OK  {results[n]['delay_ms']:>5}  fail={results[n]['fail_rounds']}/{rounds}  {n}"
        )
    print("\n--- DEAD ALL ROUNDS (purge candidates) ---")
    for n in dead:
        print(f"  DEAD {results[n]['status'][:80]}  {n}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "ts": ts,
        "url": args.url,
        "timeout_ms": args.timeout_ms,
        "max_latency_ms": args.max_latency_ms,
        "rounds": rounds,
        "healthy_count": len(healthy),
        "dead_count": len(dead),
        "healthy": [
            {
                "name": n,
                "delay_ms": results[n]["delay_ms"],
                "fail_rounds": results[n]["fail_rounds"],
                "class": classify(n),
            }
            for n in healthy
        ],
        "dead": [
            {
                "name": n,
                "status": results[n]["status"],
                "fail_rounds": results[n]["fail_rounds"],
                "class": classify(n),
            }
            for n in dead
        ],
        "preferred_first": preferred_first,
        "healthy_reg": healthy_reg,
        "register_pool_excluded": [
            n for n in healthy if n not in set(healthy_reg)
        ],
    }
    report_path = REPORT_DIR / f"clash_node_health_{ts}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    latest = REPORT_DIR / "clash_node_health_latest.json"
    latest.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORT_DIR / "clash_nodes_healthy.txt").write_text(
        "\n".join(healthy) + ("\n" if healthy else ""), encoding="utf-8"
    )
    (REPORT_DIR / "clash_nodes_dead.txt").write_text(
        "\n".join(dead) + ("\n" if dead else ""), encoding="utf-8"
    )
    print(f"\nreport={report_path}")

    if args.dry_run:
        print("dry-run: skip group select / config rewrite / purge")
        return 0 if healthy else 2

    pick = args.select.strip()
    if pick and pick not in set(healthy_reg):
        if pick in set(healthy) and pick not in set(healthy_reg):
            print(
                f"WARN requested --select {pick!r} is register-pool-excluded "
                f"(dog VPS / dialer hairpin); ignoring"
            )
        else:
            print(f"WARN requested --select {pick!r} not healthy; ignoring")
        pick = ""
    if not pick:
        pick = (
            preferred_first[0]
            if preferred_first
            else (healthy_reg[0] if healthy_reg else "")
        )
    if pick:
        for g in ("GLOBAL", "PROXY", "🎯Grok注册", "🔰ChatGPT", "♻️Grok优选"):
            put_group_now(secret, g, pick)
    else:
        print(
            "ERROR: no healthy register-pool nodes "
            f"(healthy={len(healthy)} reg={len(healthy_reg)}) — not changing selection"
        )
        return 2

    if args.purge_proxies and dead:
        print("\n=== purge dead proxies (failed all rounds) ===")
        for p in (CFG, MERGED):
            if p.is_file():
                purge_dead_proxies(p, dead)
        # After full delete, register groups still need healthy-only rewrite.
        if not args.apply_config:
            print("\n=== rewrite config groups (post-purge) ===")
            for p in (CFG, MERGED):
                if p.is_file():
                    rewrite_config_groups(p, healthy_reg, preferred_first)

    if args.apply_config:
        print("\n=== rewrite config groups ===")
        for p in (CFG, MERGED):
            if p.is_file():
                rewrite_config_groups(p, healthy_reg, preferred_first)
        print(
            "NOTE: restart mihomo to load rewritten groups:\n"
            "  bash start-clash-for-grok.sh"
        )

    excluded_live = [n for n in healthy if n not in set(healthy_reg)]
    if excluded_live:
        print(
            "register_pool_excluded_healthy="
            + ",".join(excluded_live[:12])
            + (f"...(+{len(excluded_live)-12})" if len(excluded_live) > 12 else "")
        )
    print(
        f"\nREADY for register batch: select={pick} "
        f"healthy={len(healthy)} reg_pool={len(healthy_reg)} dead={len(dead)} "
        f"preferred_first={preferred_first[:5]}"
    )
    return 0 if healthy_reg else 2


if __name__ == "__main__":
    raise SystemExit(main())
