"""Self-check + orphan browser/Xvfb cleanup for the control plane."""

from __future__ import annotations

import json
import os
import shutil
import socket
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


def _ok(name: str, ok: bool, detail: str = "", **extra: Any) -> dict[str, Any]:
    out = {"name": name, "ok": bool(ok), "detail": detail}
    out.update(extra)
    return out


def _tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_probe(url: str, proxy: str | None = None, timeout: float = 8.0) -> tuple[bool, str]:
    try:
        handlers = []
        if proxy:
            handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
        opener = build_opener(*handlers)
        req = Request(url, method="GET", headers={"User-Agent": "control-api-selfcheck/1"})
        with opener.open(req, timeout=timeout) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            return True, f"HTTP {code}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:200]


def selfcheck(root: Path) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    cfg_path = root / "config.json"
    cfg: dict[str, Any] = {}
    if cfg_path.is_file():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg = {k: v for k, v in raw.items() if not str(k).startswith("//")}
            checks.append(_ok("config.json", True, f"keys={len(cfg)}"))
        except Exception as exc:
            checks.append(_ok("config.json", False, str(exc)[:200]))
    else:
        checks.append(_ok("config.json", False, "missing"))

    venv_py = root / ".venv" / "bin" / "python"
    checks.append(_ok("venv_python", venv_py.is_file(), str(venv_py)))
    reg_cli = root / "register_cli.py"
    checks.append(_ok("register_cli.py", reg_cli.is_file(), str(reg_cli)))
    sup = root / "scripts" / "launch_batch_supervisor.sh"
    checks.append(_ok("launch_batch_supervisor.sh", sup.is_file(), str(sup)))
    auths = root / "cpa_auths"
    if auths.is_dir():
        n = sum(1 for _ in auths.glob("xai-*.json"))
        checks.append(_ok("cpa_auths", True, f"files={n}", path=str(auths)))
    else:
        checks.append(_ok("cpa_auths", False, "missing dir", path=str(auths)))

    # Clash mixed-port default
    clash_port = 7897
    clash_up = _tcp_open("127.0.0.1", clash_port)
    checks.append(
        _ok(
            "clash_mixed_7897",
            clash_up,
            "up" if clash_up else "down (ordinary batch needs mihomo mixed-port)",
        )
    )
    clash_api = _tcp_open("127.0.0.1", 9090) or _tcp_open("127.0.0.1", 9097)
    checks.append(_ok("clash_api", clash_api, "9090/9097 reachable" if clash_api else "api port closed"))

    # Display / browsers for headed mint
    display = os.environ.get("DISPLAY") or ""
    checks.append(_ok("DISPLAY", bool(display) or shutil.which("Xvfb") is not None, display or "no DISPLAY; Xvfb available" if shutil.which("Xvfb") else "no DISPLAY/Xvfb"))
    checks.append(_ok("xvfb-run", shutil.which("xvfb-run") is not None, shutil.which("xvfb-run") or "missing"))

    proxy = str(cfg.get("proxy") or cfg.get("cpa_proxy") or "http://127.0.0.1:7897")
    if clash_up:
        ok, detail = _http_probe("https://accounts.x.ai/", proxy=proxy, timeout=10)
        checks.append(_ok("egress_accounts_xai", ok, detail, proxy=proxy))
    else:
        checks.append(_ok("egress_accounts_xai", False, "skipped: clash down", proxy=proxy))

    # Mail provider hint (singleton + multi pool)
    provider = str(cfg.get("email_provider") or os.environ.get("EMAIL_PROVIDER") or "")
    raw_multi = cfg.get("email_providers")
    if raw_multi is None or raw_multi == "":
        raw_multi = os.environ.get("EMAIL_PROVIDERS") or ""
    if isinstance(raw_multi, (list, tuple, set)):
        multi = [str(x).strip() for x in raw_multi if str(x).strip()]
    else:
        multi = [p.strip() for p in str(raw_multi).replace("，", ",").split(",") if p.strip()]
    multi_s = ",".join(multi) if multi else ""
    domains = str(cfg.get("defaultDomains") or os.environ.get("DEFAULT_DOMAINS") or "")
    checks.append(
        _ok(
            "email_config",
            bool(provider or multi),
            (
                f"provider={provider or '?'} "
                f"providers={multi_s or '(empty)'} "
                f"domains={domains or '(empty)'}"
            ),
        )
    )

    # Live run lock
    lock_pid = None
    lock_alive = False
    pid_path = Path("/tmp/grok_batch_supervisor.lock.pid")
    if pid_path.is_file():
        try:
            lock_pid = int(pid_path.read_text().strip())
            os.kill(lock_pid, 0)
            lock_alive = True
        except Exception:
            lock_alive = False
    checks.append(
        _ok(
            "supervisor_lock",
            True,
            f"pid={lock_pid} alive={lock_alive}" if lock_pid is not None else "no lock",
            pid=lock_pid,
            alive=lock_alive,
        )
    )

    failed = [c for c in checks if not c.get("ok")]
    return {
        "ok": len(failed) == 0,
        "ts": int(time.time()),
        "failed": len(failed),
        "checks": checks,
        "summary": "all green" if not failed else f"{len(failed)} check(s) failed",
    }


def cleanup_orphans(*, dry_run: bool = False) -> dict[str, Any]:
    """Kill PPID=1 leftover Drission Chromes + empty Xvfb (safe for live register_cli children)."""
    try:
        from tab_pool import cleanup_orphan_drission_chromes, cleanup_orphan_xvfb
    except Exception as exc:
        return {"ok": False, "detail": f"import tab_pool failed: {exc}", "dry_run": dry_run}

    chrome = cleanup_orphan_drission_chromes(
        dry_run=dry_run,
        only_ppid_init=True,
        include_self_children=False,
        kill_related_helpers=True,
        clean_tmp_dirs=True,
        tmp_dir_max_age_sec=0 if not dry_run else 0,
    )
    xvfb = cleanup_orphan_xvfb(dry_run=dry_run, only_ppid_init=True, require_no_children=True)
    return {
        "ok": True,
        "dry_run": dry_run,
        "chrome": chrome,
        "xvfb": xvfb,
        "detail": (
            f"chrome_killed={chrome.get('killed', 0)} xvfb_killed={xvfb.get('killed', 0)}"
            if not dry_run
            else f"chrome_would={chrome.get('would_kill', chrome.get('matched', 0))} "
            f"xvfb_would={xvfb.get('would_kill', xvfb.get('matched', 0))}"
        ),
    }
