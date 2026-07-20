"""Start/stop/status/logs for supervised and one-shot register runs."""

from __future__ import annotations

import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from apps.control_api.process_registry import (
    ProcessRegistry,
    stop_pid,
    supervisor_lock_held,
    supervisor_lock_pid,
)
from apps.control_api.schemas import StartRunRequest

EXTRA_ENV_ALLOWLIST = frozenset(
    {
        "SKIP_CLASH_PREFLIGHT",
        "CPA_PROBE_CHAT",
        "CPA_BATCH_END_INJECT",
        "SUPERVISOR_CHUNK",
        "EMAIL_PROVIDER",
        "DEFAULT_DOMAINS",
        "NODE_SCORE",
    }
)

_COMPLETE_RE = re.compile(r"complete=(\d+)")
_ZERO_RE = re.compile(r"consecutive_zero=(\d+)|zero=(\d+)")


def filter_extra_env(extra: dict[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (extra or {}).items():
        key = str(k)
        if key not in EXTRA_ENV_ALLOWLIST:
            raise ValueError(f"extra_env key not allowed: {key}")
        out[key] = str(v)
    return out


def _latest_supervisor_log(root: Path) -> Path | None:
    logs = root / "logs"
    if not logs.is_dir():
        return None
    cands = sorted(logs.glob("*supervisor.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def _parse_supervisor_progress(text: str) -> dict[str, Any]:
    complete = None
    zero = None
    for line in reversed(text.splitlines()):
        if complete is None and "[supervisor]" in line:
            m = _COMPLETE_RE.search(line)
            if m:
                complete = int(m.group(1))
        if zero is None and "[supervisor]" in line:
            m2 = re.search(r"consecutive_zero=(\d+)", line)
            if m2:
                zero = int(m2.group(1))
            else:
                m3 = re.search(r"\bzero=(\d+)", line)
                if m3:
                    zero = int(m3.group(1))
        if complete is not None and zero is not None:
            break
    return {"complete": complete, "consecutive_zero": zero}


def run_status(root: Path) -> dict[str, Any] | None:
    reg = ProcessRegistry(root)
    cur = reg.current()
    lock_pid = supervisor_lock_pid()
    sup_log = _latest_supervisor_log(root)
    progress: dict[str, Any] = {}
    last_lines: list[str] = []
    if sup_log and sup_log.is_file():
        try:
            text = sup_log.read_text(encoding="utf-8", errors="replace")
            progress = _parse_supervisor_progress(text)
            last_lines = text.splitlines()[-5:]
        except Exception:
            pass

    if cur:
        return {
            "source": "registry",
            "alive": True,
            "run_id": cur.get("run_id"),
            "pid": cur.get("pid"),
            "kind": cur.get("kind"),
            "meta": cur.get("meta") or {},
            "lock_pid": lock_pid,
            "supervisor_log": str(sup_log) if sup_log else None,
            **progress,
            "last_lines": last_lines,
        }

    if lock_pid is not None:
        return {
            "source": "lock",
            "alive": True,
            "run_id": None,
            "pid": lock_pid,
            "kind": "grok_supervisor",
            "meta": {},
            "lock_pid": lock_pid,
            "supervisor_log": str(sup_log) if sup_log else None,
            **progress,
            "last_lines": last_lines,
        }

    if progress.get("complete") is not None or last_lines:
        return {
            "source": "log",
            "alive": False,
            "run_id": None,
            "pid": None,
            "kind": None,
            "meta": {},
            "lock_pid": None,
            "supervisor_log": str(sup_log) if sup_log else None,
            **progress,
            "last_lines": last_lines,
        }
    return None


def tail_log(root: Path, n: int = 200) -> dict[str, str | None]:
    reg = ProcessRegistry(root)
    cur = reg.current()
    path: Path | None = None
    if cur and cur.get("meta", {}).get("log_path"):
        path = Path(cur["meta"]["log_path"])
    if path is None or not path.is_file():
        path = _latest_supervisor_log(root)
    if path is None or not path.is_file():
        return {"path": None, "text": ""}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return {"path": str(path), "text": "\n".join(lines[-n:])}


def start_run(root: Path, req: StartRunRequest) -> dict[str, Any]:
    extra = filter_extra_env(req.extra_env)
    reg = ProcessRegistry(root)
    existing = reg.current()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"run already active pid={existing.get('pid')} kind={existing.get('kind')}",
        )
    if req.kind == "grok_supervisor" and supervisor_lock_held():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"supervisor lock held pid={supervisor_lock_pid()}",
        )

    run_id = uuid.uuid4().hex[:12]
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"control_api_{run_id}.log"

    if req.kind == "grok_supervisor":
        argv = [
            "bash",
            str(root / "scripts" / "launch_batch_supervisor.sh"),
            req.mode,
            str(req.target),
            str(req.threads),
            req.tag,
        ]
        script = root / "scripts" / "launch_batch_supervisor.sh"
        if not script.is_file():
            raise HTTPException(status_code=500, detail=f"missing {script}")
    else:
        register_sh = root / "register.sh"
        if not register_sh.is_file():
            raise HTTPException(status_code=500, detail=f"missing {register_sh}")
        argv = ["bash", str(register_sh), req.product, str(req.target), str(req.threads)]

    env = os.environ.copy()
    env.update(extra)
    env["REGISTER_PROJECT_ROOT"] = str(root)

    log_f = open(log_path, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(root),
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        log_f.close()
        raise

    meta = {
        "argv": argv,
        "log_path": str(log_path),
        "product": req.product,
        "mode": req.mode,
        "target": req.target,
        "threads": req.threads,
        "tag": req.tag,
    }
    reg.register(run_id, proc.pid, req.kind, meta)
    return {"ok": True, "run": reg.current(), "detail": "started"}


def stop_run(root: Path) -> dict[str, Any]:
    reg = ProcessRegistry(root)
    cur = reg.current()
    pid: int | None = None
    if cur:
        pid = int(cur.get("pid") or 0)
    if not pid:
        pid = supervisor_lock_pid()
    if not pid:
        return {"ok": False, "run": None, "detail": "no active run"}
    result = stop_pid(pid, grace_sec=10.0)
    reg.clear()
    return {"ok": bool(result.get("ok")), "run": None, "detail": result.get("detail", ""), "pid": pid}


def list_runs(root: Path) -> list[dict[str, Any]]:
    """Recent supervisor logs index (lightweight)."""
    logs = root / "logs"
    if not logs.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(logs.glob("*supervisor.log"), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
        out.append(
            {
                "path": str(p),
                "name": p.name,
                "mtime": p.stat().st_mtime,
            }
        )
    return out
