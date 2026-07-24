"""Runs / process registry tests."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from unittest import mock

import pytest
from fastapi import HTTPException

from apps.control_api.process_registry import ProcessRegistry, stop_pid
from apps.control_api.runs import filter_extra_env, run_status, start_run, stop_run
from apps.control_api.schemas import StartRunRequest


def test_extra_env_reject_unknown():
    with pytest.raises(ValueError, match="not allowed"):
        filter_extra_env({"EVIL": "1"})


def test_extra_env_allowlist():
    assert filter_extra_env({"SKIP_CLASH_PREFLIGHT": "1"}) == {"SKIP_CLASH_PREFLIGHT": "1"}
    assert filter_extra_env(
        {
            "CPA_BATCH_END_INJECT": "true",
            "CPA_BATCH_IMPORT_EVERY": "100",
            "CPA_BATCH_IMPORT_SIZE": "100",
            "CPA_BATCH_IMPORT_PAUSE": "3",
        }
    ) == {
        "CPA_BATCH_END_INJECT": "true",
        "CPA_BATCH_IMPORT_EVERY": "100",
        "CPA_BATCH_IMPORT_SIZE": "100",
        "CPA_BATCH_IMPORT_PAUSE": "3",
    }


def test_start_409_when_registry_active(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "launch_batch_supervisor.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (tmp_path / "logs").mkdir()
    reg = ProcessRegistry(tmp_path)
    reg.register("abc", os.getpid(), "grok_supervisor", {})
    with pytest.raises(HTTPException) as ei:
        start_run(
            tmp_path,
            StartRunRequest(kind="grok_supervisor", target=10, tag="t"),
        )
    assert ei.value.status_code == 409


def test_start_409_when_lock_pid_alive(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "launch_batch_supervisor.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    lock = Path("/tmp/grok_batch_supervisor.lock.pid")
    # Only mock supervisor_lock_held path via monkeypatch of helper
    monkeypatch.setattr(
        "apps.control_api.runs.supervisor_lock_held",
        lambda: True,
    )
    monkeypatch.setattr(
        "apps.control_api.runs.supervisor_lock_pid",
        lambda: 999999,
    )
    with pytest.raises(HTTPException) as ei:
        start_run(tmp_path, StartRunRequest(kind="grok_supervisor", target=10))
    assert ei.value.status_code == 409
    del lock  # silence unused


def test_stop_only_recorded_pid(tmp_path: Path, monkeypatch):
    reg = ProcessRegistry(tmp_path)
    reg.register("r1", 424242, "register_sh", {"process_group": True})
    seen: dict = {}

    def fake_stop(pid, grace_sec=10.0, *, process_group=True):
        seen["pid"] = pid
        seen["grace_sec"] = grace_sec
        seen["process_group"] = process_group
        return {"ok": True, "detail": "terminated", "pid": pid, "mode": "pg"}

    # Keep fake registry pid "alive" so clear_if_dead does not drop it.
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(lambda pid: pid == 424242),
    )
    monkeypatch.setattr("apps.control_api.runs.stop_pid", fake_stop)
    out = stop_run(tmp_path)
    assert out["ok"] is True
    assert out["pid"] == 424242
    assert out["source"] == "registry"
    assert out["mode"] == "pg"
    assert seen["process_group"] is True
    # After stop, registry cleared; current() may still see nothing.
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(lambda pid: False),
    )
    assert ProcessRegistry(tmp_path).current() is None


def test_stop_lock_fallback(tmp_path: Path, monkeypatch):
    # No registry current → use lock pid
    seen: dict = {}

    def fake_stop(pid, grace_sec=10.0, *, process_group=True):
        seen["pid"] = pid
        seen["process_group"] = process_group
        return {"ok": True, "detail": "terminated", "pid": pid, "mode": "pid"}

    monkeypatch.setattr("apps.control_api.runs.stop_pid", fake_stop)
    monkeypatch.setattr("apps.control_api.runs.supervisor_lock_pid", lambda: 777001)
    out = stop_run(tmp_path)
    assert out["ok"] is True
    assert out["pid"] == 777001
    assert out["source"] == "lock"
    assert seen["process_group"] is True


def test_stop_pid_prefers_killpg(monkeypatch):
    calls: list[tuple[str, int, int]] = []

    def fake_killpg(pgid, sig):
        calls.append(("pg", pgid, sig))

    def fake_kill(pid, sig):
        calls.append(("pid", pid, sig))
        raise AssertionError("single-pid kill should not run when killpg works")

    # alive before signal, dead after killpg
    states = iter([True, False])
    monkeypatch.setattr("apps.control_api.process_registry.os.killpg", fake_killpg)
    monkeypatch.setattr("apps.control_api.process_registry.os.kill", fake_kill)
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(lambda pid: next(states, False)),
    )
    out = stop_pid(4242, grace_sec=0.01, process_group=True)
    assert out["ok"] is True
    assert out["mode"] == "pg"
    assert out["detail"] == "terminated"
    assert calls and calls[0][0] == "pg"
    assert calls[0][1] == 4242


def test_stop_pid_falls_back_to_single_pid(monkeypatch):
    calls: list[tuple[str, int]] = []

    def fake_killpg(pgid, sig):
        calls.append(("pg", pgid))
        raise PermissionError("not leader")

    def fake_kill(pid, sig):
        calls.append(("pid", pid))

    states = iter([True, False])
    monkeypatch.setattr("apps.control_api.process_registry.os.killpg", fake_killpg)
    monkeypatch.setattr("apps.control_api.process_registry.os.kill", fake_kill)
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(lambda pid: next(states, False)),
    )
    out = stop_pid(9001, grace_sec=0.01, process_group=True)
    assert out["ok"] is True
    assert out["mode"] == "pid"
    assert ("pg", 9001) in calls
    assert ("pid", 9001) in calls


def test_stop_pid_killpg_esrch_falls_back_when_pid_alive(monkeypatch):
    """External flock supervisor is often not the group leader.

    On Linux, killpg(non-leader-pid) raises ProcessLookupError (ESRCH) even when
    that pid is still alive. Treating ESRCH as "already dead" makes the UI report
    stop success while batch_dc1k_ns keeps running.
    """
    calls: list[tuple[str, int, int]] = []

    def fake_killpg(pgid, sig):
        calls.append(("pg", pgid, sig))
        raise ProcessLookupError(3, "No such process")

    def fake_kill(pid, sig):
        calls.append(("pid", pid, sig))

    # Stay alive until single-pid SIGTERM lands, then die.
    alive = {"v": True}

    def fake_alive(pid):
        return alive["v"]

    def kill_and_die(pid, sig):
        calls.append(("pid", pid, sig))
        alive["v"] = False

    monkeypatch.setattr("apps.control_api.process_registry.os.killpg", fake_killpg)
    monkeypatch.setattr("apps.control_api.process_registry.os.kill", kill_and_die)
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(fake_alive),
    )
    out = stop_pid(1554550, grace_sec=0.05, process_group=True)
    assert out["ok"] is True
    assert out["detail"] == "terminated"
    assert out["mode"] == "pid"
    assert out["detail"] != "already dead"
    assert any(c[0] == "pg" for c in calls)
    assert any(c[0] == "pid" and c[1] == 1554550 for c in calls)


def test_stop_pid_uses_real_pgid_for_external_tree(monkeypatch):
    """Prefer os.getpgid(pid) so killpg hits the real group of a non-leader supervisor."""
    calls: list[tuple[str, int, int]] = []

    def fake_getpgid(pid):
        assert pid == 1554550
        return 1554494

    def fake_killpg(pgid, sig):
        calls.append(("pg", pgid, sig))

    def fake_kill(pid, sig):
        calls.append(("pid", pid, sig))
        raise AssertionError("single-pid kill should not run when real pgid killpg works")

    states = iter([True, False])
    monkeypatch.setattr("apps.control_api.process_registry.os.getpgid", fake_getpgid)
    monkeypatch.setattr("apps.control_api.process_registry.os.killpg", fake_killpg)
    monkeypatch.setattr("apps.control_api.process_registry.os.kill", fake_kill)
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(lambda pid: next(states, False)),
    )
    out = stop_pid(1554550, grace_sec=0.01, process_group=True)
    assert out["ok"] is True
    assert out["mode"] == "pg"
    assert calls[0] == ("pg", 1554494, signal.SIGTERM)


def test_stop_pid_killpg_esrch_falls_back_when_pid_alive(monkeypatch):
    """External flock supervisor is often not the group leader.

    On Linux, killpg(non-leader-pid) raises ProcessLookupError (ESRCH) even when
    that pid is still alive. Treating ESRCH as "already dead" makes the UI report
    stop success while batch_dc1k_ns keeps running.
    """
    calls: list[tuple[str, int, int]] = []

    def fake_killpg(pgid, sig):
        calls.append(("pg", pgid, sig))
        raise ProcessLookupError(3, "No such process")

    def fake_kill(pid, sig):
        calls.append(("pid", pid, sig))

    # Stay alive until single-pid SIGTERM lands, then die.
    alive = {"v": True}

    def fake_alive(pid):
        return alive["v"]

    def kill_and_die(pid, sig):
        calls.append(("pid", pid, sig))
        alive["v"] = False

    monkeypatch.setattr("apps.control_api.process_registry.os.killpg", fake_killpg)
    monkeypatch.setattr("apps.control_api.process_registry.os.kill", kill_and_die)
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(fake_alive),
    )
    out = stop_pid(1554550, grace_sec=0.05, process_group=True)
    assert out["ok"] is True
    assert out["detail"] == "terminated"
    assert out["mode"] == "pid"
    assert out["detail"] != "already dead"
    assert any(c[0] == "pg" for c in calls)
    assert any(c[0] == "pid" and c[1] == 1554550 for c in calls)


def test_stop_pid_uses_real_pgid_for_external_tree(monkeypatch):
    """Prefer os.getpgid(pid) so killpg hits the real group of a non-leader supervisor."""
    calls: list[tuple[str, int, int]] = []

    def fake_getpgid(pid):
        assert pid == 1554550
        return 1554494

    def fake_killpg(pgid, sig):
        calls.append(("pg", pgid, sig))

    def fake_kill(pid, sig):
        calls.append(("pid", pid, sig))
        raise AssertionError("single-pid kill should not run when real pgid killpg works")

    states = iter([True, False])
    monkeypatch.setattr("apps.control_api.process_registry.os.getpgid", fake_getpgid)
    monkeypatch.setattr("apps.control_api.process_registry.os.killpg", fake_killpg)
    monkeypatch.setattr("apps.control_api.process_registry.os.kill", fake_kill)
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(lambda pid: next(states, False)),
    )
    out = stop_pid(1554550, grace_sec=0.01, process_group=True)
    assert out["ok"] is True
    assert out["mode"] == "pg"
    assert calls[0] == ("pg", 1554494, signal.SIGTERM)


def test_start_popen_argv(tmp_path: Path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "launch_batch_supervisor.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    (tmp_path / "logs").mkdir()

    class FakeProc:
        pid = 555

        def poll(self):
            return None  # still running through fail-fast window

        def wait(self, timeout=None):
            return 0

    captured = {}
    closed = {"n": 0}

    class FakeFile:
        def close(self):
            closed["n"] += 1

    def fake_open(*_a, **_k):
        return FakeFile()

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr("apps.control_api.runs.subprocess.Popen", fake_popen)
    monkeypatch.setattr("apps.control_api.runs.supervisor_lock_held", lambda: False)
    monkeypatch.setattr("apps.control_api.runs.time.sleep", lambda _s: None)
    monkeypatch.setattr("builtins.open", fake_open)
    # Fake child pid must look alive for registry.current() after register.
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(lambda pid: pid == 555),
    )
    result = start_run(
        tmp_path,
        StartRunRequest(
            kind="grok_supervisor",
            mode="ordinary",
            target=50,
            threads=1,
            tag="batch_web",
            extra_env={"NODE_SCORE": "1"},
        ),
    )
    assert result["ok"] is True
    assert captured["argv"][0] == "bash"
    assert captured["argv"][2] == "ordinary"
    assert captured["argv"][3] == "50"
    assert captured["argv"][4] == "1"
    assert captured["argv"][5] == "batch_web"
    assert captured["kwargs"].get("start_new_session") is True
    # Parent log handle closed after successful Popen (child keeps its dup).
    assert closed["n"] == 1
    meta = result["run"]["meta"]
    assert meta.get("process_group") is True
    assert meta.get("pgid") == 555


def test_run_status_flattens_recent_writes(tmp_path: Path):
    """`run_status` should surface `recent_writes` at the top level for UI."""
    (tmp_path / "logs").mkdir()
    # No lock, no registry, no supervisor log ⇒ None is acceptable
    st = run_status(tmp_path)
    if st is None:
        return
    assert "recent_writes" in st
    assert isinstance(st["recent_writes"], list)


def test_run_status_recent_writes_from_progress(tmp_path: Path, monkeypatch):
    """When progress supplies recent_writes list, it is flattened to top-level."""
    (tmp_path / "logs").mkdir()
    sample = ["cpa_auths/xai-abc.json", "cpa_auths/xai-def.json"]

    def fake_progress(root, sup_log=None):
        return {
            "complete": 42,
            "recent_writes": list(sample),
            "last_lines": ["hello"],
            "steps": [],
            "timeline": [],
        }

    monkeypatch.setattr("apps.control_api.runs.build_progress", fake_progress)
    # Force a "log" source (no registry / no lock) via minimal shim.
    monkeypatch.setattr("apps.control_api.runs.supervisor_lock_pid", lambda: None)
    st = run_status(tmp_path)
    assert st is not None, "expected log-source run when progress reports complete"
    assert st.get("recent_writes") == sample


def test_pid_alive_treats_zombie_as_dead(tmp_path: Path, monkeypatch):
    """Zombie bash (kill 0 OK, /proc state Z) must not keep registry ALIVE."""
    reg = ProcessRegistry(tmp_path)
    reg.register("z1", 424242, "grok_supervisor", {})

    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry._reap_children",
        staticmethod(lambda: None),
    )
    monkeypatch.setattr(
        "apps.control_api.process_registry.os.kill",
        lambda pid, sig: None if pid == 424242 else (_ for _ in ()).throw(ProcessLookupError()),
    )
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry._pid_is_zombie",
        staticmethod(lambda pid: pid == 424242),
    )
    assert ProcessRegistry.pid_alive(424242) is False
    assert ProcessRegistry(tmp_path).current() is None


def test_supervisor_flock_busy_true_when_held(tmp_path: Path, monkeypatch):
    from apps.control_api.process_registry import supervisor_flock_busy, supervisor_lock_held

    monkeypatch.setattr(
        "apps.control_api.process_registry.supervisor_lock_pid",
        lambda: None,
    )
    # Simulate BlockingIOError on flock NB acquire.
    import fcntl as _fcntl

    real_flock = _fcntl.flock

    def fake_flock(fd, op):
        if op == (_fcntl.LOCK_EX | _fcntl.LOCK_NB):
            raise BlockingIOError()
        return real_flock(fd, op)

    monkeypatch.setattr("apps.control_api.process_registry.fcntl.flock", fake_flock)
    assert supervisor_flock_busy() is True
    assert supervisor_lock_held() is True


def test_start_clears_registry_when_child_exits_immediately(tmp_path: Path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "launch_batch_supervisor.sh").write_text(
        "#!/bin/bash\n", encoding="utf-8"
    )
    (tmp_path / "logs").mkdir()

    class DeadProc:
        pid = 666

        def poll(self):
            return 1

        def wait(self, timeout=None):
            return 1

    # Pre-seed the log path pattern: start_run creates control_api_<id>.log.
    # After Popen we inject failure text via a side-channel: wrap only the
    # log open used by start_run (apps.control_api.runs open call uses open()).
    real_open = open

    class LogFile:
        def __init__(self, path: Path):
            self.path = path
            self._f = real_open(path, "a", encoding="utf-8")
            self.path.write_text(
                "another supervisor holds /tmp/grok_batch_supervisor.lock; exit\n",
                encoding="utf-8",
            )

        def close(self):
            self._f.close()

        def write(self, data):
            return self._f.write(data)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()

    def fake_open(path, *a, **k):
        p = Path(path)
        if p.name.startswith("control_api_") and p.suffix == ".log":
            p.parent.mkdir(parents=True, exist_ok=True)
            return LogFile(p)
        return real_open(path, *a, **k)

    monkeypatch.setattr("apps.control_api.runs.subprocess.Popen", lambda *a, **k: DeadProc())
    monkeypatch.setattr("apps.control_api.runs.supervisor_lock_held", lambda: False)
    monkeypatch.setattr("apps.control_api.runs.time.sleep", lambda _s: None)
    monkeypatch.setattr("builtins.open", fake_open)

    with pytest.raises(HTTPException) as ei:
        start_run(tmp_path, StartRunRequest(kind="grok_supervisor", target=10, tag="t"))
    assert ei.value.status_code == 500
    assert "exited immediately" in str(ei.value.detail)
    assert "another supervisor holds" in str(ei.value.detail)
    assert ProcessRegistry(tmp_path).current() is None
