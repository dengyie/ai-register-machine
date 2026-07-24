"""In-memory + on-disk registry of control-plane started runs."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

_LOCK = Path("/tmp/grok_batch_supervisor.lock")
_LOCK_PID = Path("/tmp/grok_batch_supervisor.lock.pid")


class ProcessRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.path = root / "logs" / "control_api_runs.json"
        self._current: dict[str, Any] | None = None
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(data, dict) and data.get("current"):
            self._current = data["current"]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"current": self._current, "updated_at": time.time()}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def register(self, run_id: str, pid: int, kind: str, meta: dict[str, Any]) -> None:
        self._current = {
            "run_id": run_id,
            "pid": int(pid),
            "kind": kind,
            "meta": meta,
            "started_at": time.time(),
        }
        self._save()

    def current(self) -> dict[str, Any] | None:
        self.clear_if_dead()
        return self._current

    def clear(self) -> None:
        self._current = None
        self._save()

    @staticmethod
    def _reap_children() -> None:
        """Non-blocking reap of any exited children (zombies from Popen).

        control_api starts supervisors with Popen and never waits; when the
        child exits immediately (e.g. lock held), it becomes a zombie still
        owned by control_api. kill(pid, 0) returns True for zombies, so the
        registry would stay stuck forever unless we reap + treat Z as dead.
        """
        try:
            while True:
                wpid, _st = os.waitpid(-1, os.WNOHANG)
                if wpid <= 0:
                    break
        except ChildProcessError:
            # No children — normal when registry pid is not ours.
            pass
        except OSError:
            pass

    @staticmethod
    def _pid_is_zombie(pid: int) -> bool:
        """Linux /proc: state 'Z' means zombie (still in table, not runnable)."""
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError):
            return False
        # comm may contain spaces/parens; state is the first token after last ')'
        rparen = raw.rfind(")")
        if rparen < 0:
            return False
        fields = raw[rparen + 2 :].split()
        return bool(fields) and fields[0] == "Z"

    @staticmethod
    def pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        # Best-effort: if this is our zombie child, reap it so the pid vanishes.
        ProcessRegistry._reap_children()
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Exists but not signalable — treat as alive (not our zombie).
            return True
        # Zombies still pass kill(0); they are not a live supervisor.
        if ProcessRegistry._pid_is_zombie(pid):
            return False
        return True

    def clear_if_dead(self) -> None:
        if not self._current:
            return
        pid = int(self._current.get("pid") or 0)
        if not self.pid_alive(pid):
            self._current = None
            self._save()


def supervisor_lock_pid() -> int | None:
    if not _LOCK_PID.is_file():
        return None
    try:
        raw = _LOCK_PID.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except Exception:
        return None
    if ProcessRegistry.pid_alive(pid):
        return pid
    return None


def supervisor_flock_busy() -> bool:
    """True if another process currently holds the exclusive flock on the lock file.

    Complements ``supervisor_lock_pid``: after a batch exits, ``.lock.pid`` is often
    stale while a *leaked* holder (e.g. mihomo inheriting fd 9 from preflight restart)
    still keeps the flock. Probing the flock itself catches that case.
    """
    try:
        fd = os.open(str(_LOCK), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def supervisor_lock_held() -> bool:
    """True if a live supervisor is indicated by lock.pid **or** the flock is busy."""
    if supervisor_lock_pid() is not None:
        return True
    return supervisor_flock_busy()


def _signal_tree(pid: int, sig: int, *, process_group: bool) -> str:
    """Deliver *sig* to the process group when possible, else the single pid.

    Control-started runs use ``start_new_session=True``, so the registered pid is
    the session/group leader and ``killpg`` reaps chrome/xvfb/register children.

    External flock supervisors (``batch_dc1k_ns``) are often **not** the group
    leader: their pid != pgid. On Linux ``killpg(non_leader_pid, …)`` raises
    ``ProcessLookupError`` (ESRCH) even while that pid is still alive. Treating
    that as "already dead" makes ``/api/runs/stop`` report success while the
    batch keeps running. Resolve the real pgid via ``getpgid`` first; if group
    delivery still fails and the pid is alive, fall back to single-pid signal.
    """
    if process_group and pid > 0:
        pgid = pid
        try:
            pgid = int(os.getpgid(pid))
        except (ProcessLookupError, PermissionError, OSError):
            pgid = pid
        try:
            os.killpg(pgid, sig)
            return "pg"
        except ProcessLookupError:
            # ESRCH on killpg is ambiguous: real missing group OR bad pgid.
            # Only re-raise (→ already dead) when the target pid itself is gone.
            if not ProcessRegistry.pid_alive(pid):
                raise
            # fall through to single-pid
        except (PermissionError, OSError):
            pass
    os.kill(pid, sig)
    return "pid"


def stop_pid(
    pid: int,
    grace_sec: float = 10.0,
    *,
    process_group: bool = True,
) -> dict[str, Any]:
    """SIGTERM then optional SIGKILL.

    Prefer process-group delivery (``killpg``) so supervisor children started under
    a new session are not left orphaned. Falls back to the single pid when the
    target is not a group leader (common for external lock-only runs).
    """
    if pid <= 0:
        return {"ok": False, "detail": "invalid pid"}
    if not ProcessRegistry.pid_alive(pid):
        return {"ok": True, "detail": "already dead", "pid": pid, "mode": "none"}
    try:
        mode = _signal_tree(pid, signal.SIGTERM, process_group=process_group)
    except ProcessLookupError:
        return {"ok": True, "detail": "already dead", "pid": pid, "mode": "none"}
    deadline = time.time() + grace_sec
    while time.time() < deadline:
        if not ProcessRegistry.pid_alive(pid):
            return {"ok": True, "detail": "terminated", "pid": pid, "mode": mode}
        time.sleep(0.2)
    if ProcessRegistry.pid_alive(pid):
        try:
            mode = _signal_tree(pid, signal.SIGKILL, process_group=process_group)
        except ProcessLookupError:
            pass
        return {"ok": True, "detail": "killed", "pid": pid, "mode": mode}
    return {"ok": True, "detail": "terminated", "pid": pid, "mode": mode}
