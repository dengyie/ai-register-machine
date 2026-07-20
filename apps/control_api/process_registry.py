"""In-memory + on-disk registry of control-plane started runs."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path
from typing import Any

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
    def pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
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


def supervisor_lock_held() -> bool:
    return supervisor_lock_pid() is not None


def stop_pid(pid: int, grace_sec: float = 10.0) -> dict[str, Any]:
    """SIGTERM then optional SIGKILL. Only the given pid."""
    if pid <= 0:
        return {"ok": False, "detail": "invalid pid"}
    if not ProcessRegistry.pid_alive(pid):
        return {"ok": True, "detail": "already dead", "pid": pid}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"ok": True, "detail": "already dead", "pid": pid}
    deadline = time.time() + grace_sec
    while time.time() < deadline:
        if not ProcessRegistry.pid_alive(pid):
            return {"ok": True, "detail": "terminated", "pid": pid}
        time.sleep(0.2)
    if ProcessRegistry.pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return {"ok": True, "detail": "killed", "pid": pid}
    return {"ok": True, "detail": "terminated", "pid": pid}
