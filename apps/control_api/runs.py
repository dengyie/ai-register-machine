"""Start/stop/status/logs for supervised and one-shot register runs."""

from __future__ import annotations

import os
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
from apps.control_api.progress import build_progress
from apps.control_api.schemas import StartRunRequest

EXTRA_ENV_ALLOWLIST = frozenset(
    {
        "SKIP_CLASH_PREFLIGHT",
        "CPA_PROBE_CHAT",
        "CPA_BATCH_END_INJECT",
        "CPA_BATCH_IMPORT_EVERY",
        "CPA_BATCH_IMPORT_SIZE",
        "CPA_BATCH_IMPORT_PAUSE",
        "SUPERVISOR_CHUNK",
        "EMAIL_PROVIDER",
        "DEFAULT_DOMAINS",
        "NODE_SCORE",
    }
)


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


def run_status(root: Path) -> dict[str, Any] | None:
    reg = ProcessRegistry(root)
    cur = reg.current()
    lock_pid = supervisor_lock_pid()
    sup_log = _latest_supervisor_log(root)
    progress = build_progress(root, sup_log=sup_log)

    base: dict[str, Any] = {
        "lock_pid": lock_pid,
        "progress": progress,
        # flatten common counters for backward-compatible UI/overview
        "complete": progress.get("complete"),
        "consecutive_zero": progress.get("consecutive_zero"),
        "phase": progress.get("phase"),
        "phase_title": progress.get("phase_title"),
        "phase_detail": progress.get("phase_detail"),
        "stuck": progress.get("stuck"),
        "stuck_reason": progress.get("stuck_reason"),
        "supervisor_log": progress.get("supervisor_log"),
        "worker_log": progress.get("worker_log"),
        "last_lines": progress.get("last_lines") or [],
        "steps": progress.get("steps") or [],
        "timeline": progress.get("timeline") or [],
        "summary": progress.get("summary"),
        "sub": progress.get("sub"),
        "chunk": progress.get("chunk"),
        "target": progress.get("target"),
        "target_new": progress.get("target_new"),
        "batch_gained": progress.get("batch_gained"),
        "batch_remain": progress.get("batch_remain"),
        "goal_complete": progress.get("goal_complete"),
        "baseline_complete": progress.get("baseline_complete"),
        "remain": progress.get("remain"),
        "accounts": progress.get("accounts"),
        "mode": progress.get("mode"),
        # UI: surface recent xai-*.json writes at top level so front-end
        # doesn't need to reach into `progress.recent_writes`.
        "recent_writes": progress.get("recent_writes") or [],
    }

    if cur:
        return {
            "source": "registry",
            "alive": True,
            "run_id": cur.get("run_id"),
            "pid": cur.get("pid"),
            "kind": cur.get("kind"),
            "meta": cur.get("meta") or {},
            **base,
        }

    if lock_pid is not None:
        return {
            "source": "lock",
            "alive": True,
            "run_id": None,
            "pid": lock_pid,
            "kind": "grok_supervisor",
            "meta": {},
            **base,
        }

    if progress.get("complete") is not None or base["last_lines"]:
        return {
            "source": "log",
            "alive": False,
            "run_id": None,
            "pid": None,
            "kind": None,
            "meta": {},
            **base,
        }
    return None


def tail_log(
    root: Path,
    n: int = 200,
    *,
    which: str = "auto",
) -> dict[str, str | None]:
    """Tail supervisor and/or worker logs.

    which: auto|supervisor|worker|both
    """
    reg = ProcessRegistry(root)
    cur = reg.current()
    sup = _latest_supervisor_log(root)
    progress = build_progress(root, sup_log=sup)
    worker = Path(progress["worker_log"]) if progress.get("worker_log") else None

    # Prefer registry meta log for control-api-started runs
    meta_log: Path | None = None
    if cur and cur.get("meta", {}).get("log_path"):
        meta_log = Path(cur["meta"]["log_path"])

    def _read(path: Path | None) -> tuple[str | None, str]:
        if path is None or not path.is_file():
            return None, ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return str(path), "\n".join(lines[-n:])

    if which == "worker":
        p, t = _read(worker)
        return {"path": p, "text": t, "which": "worker"}
    if which == "supervisor":
        p, t = _read(sup or meta_log)
        return {"path": p, "text": t, "which": "supervisor"}
    if which == "both":
        sp, st = _read(sup or meta_log)
        wp, wt = _read(worker)
        parts = []
        if sp:
            parts.append(f"===== supervisor: {sp} =====\n{st}")
        if wp:
            parts.append(f"===== worker: {wp} =====\n{wt}")
        return {
            "path": sp or wp,
            "text": "\n\n".join(parts),
            "which": "both",
            "supervisor_path": sp,
            "worker_path": wp,
        }
    # auto: worker if alive-ish else supervisor
    if worker and worker.is_file():
        p, t = _read(worker)
        # append a bit of supervisor footer for context
        sp, st = _read(sup)
        if st:
            t = t + "\n\n----- supervisor tail -----\n" + "\n".join(st.splitlines()[-30:])
        return {"path": p, "text": t, "which": "auto", "supervisor_path": sp}
    p, t = _read(meta_log or sup)
    return {"path": p, "text": t, "which": "auto"}


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
            # New session ⇒ pid is process-group leader; stop_run uses killpg.
            start_new_session=True,
        )
    except Exception:
        log_f.close()
        raise
    else:
        # Child inherited the fd; close the parent handle so we don't leak.
        log_f.close()

    meta = {
        "argv": argv,
        "log_path": str(log_path),
        "product": req.product,
        "mode": req.mode,
        "target": req.target,
        "threads": req.threads,
        "tag": req.tag,
        # start_new_session=True ⇒ session/group leader == pid
        "process_group": True,
        "pgid": int(proc.pid),
    }
    reg.register(run_id, proc.pid, req.kind, meta)

    # Fail-fast: launch_batch_supervisor exits immediately when flock is held
    # (or script missing). Without this poll the registry sticks on a zombie
    # bash and the UI shows ALIVE + stale progress forever.
    for _ in range(10):
        ret = proc.poll()
        if ret is not None:
            reg.clear()
            try:
                proc.wait(timeout=0.2)
            except Exception:
                pass
            tail = ""
            try:
                lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
                tail = "\n".join(lines[-20:]).strip()
            except Exception:
                pass
            detail = f"process exited immediately code={ret}"
            if tail:
                detail = f"{detail}: {tail[:500]}"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=detail,
            )
        time.sleep(0.1)

    return {"ok": True, "run": reg.current(), "detail": "started"}


def stop_run(root: Path) -> dict[str, Any]:
    """Stop control-started run (process group) or fall back to lock pid.

    Control-plane starts always use a new session, so we kill the whole group.
    Lock-only (externally launched) supervisors still try process-group delivery
    first and fall back to single-pid inside ``stop_pid``.
    """
    reg = ProcessRegistry(root)
    cur = reg.current()
    pid: int | None = None
    process_group = True
    source = "none"
    if cur:
        pid = int(cur.get("pid") or 0)
        meta = cur.get("meta") if isinstance(cur.get("meta"), dict) else {}
        # Default True for control-started; honor explicit false if ever stored.
        process_group = bool(meta.get("process_group", True))
        source = "registry"
    if not pid:
        pid = supervisor_lock_pid()
        process_group = True  # best-effort; stop_pid falls back if not leader
        source = "lock"
    if not pid:
        return {"ok": False, "run": None, "detail": "no active run"}
    result = stop_pid(pid, grace_sec=10.0, process_group=process_group)
    reg.clear()
    return {
        "ok": bool(result.get("ok")),
        "run": None,
        "detail": result.get("detail", ""),
        "pid": pid,
        "source": source,
        "mode": result.get("mode"),
    }


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
