"""Runs / process registry tests."""

from __future__ import annotations

import json
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


def test_extra_env_rejects_lf_injection():
    """A value containing a newline could inject a following line when the env
    is re-serialized (.env / shell sourcing / log lines). Refuse NUL/CR/LF
    (and vertical-tab/form-feed/DEL) outright — the actual line terminator
    family."""
    with pytest.raises(ValueError, match="control char"):
        filter_extra_env({"EMAIL_PROVIDER": "cloudflare\nEVIL=1"})
    with pytest.raises(ValueError, match="control char"):
        filter_extra_env({"EMAIL_PROVIDER": "a\rb"})
    with pytest.raises(ValueError, match="control char"):
        filter_extra_env({"EMAIL_PROVIDER": "a\x00b"})
    # tab and space are NOT line terminators and must survive (common in values)
    assert filter_extra_env({"CPA_BATCH_IMPORT_SIZE": "100\t200"})[
        "CPA_BATCH_IMPORT_SIZE"
    ] == "100\t200"


def test_extra_env_rejects_overlong_value():
    """Cap value length so a multi-KB value cannot bloat env/logs indefinitely."""
    from apps.control_api.runs import EXTRA_ENV_VALUE_MAX_LEN

    ok = "x" * EXTRA_ENV_VALUE_MAX_LEN
    assert filter_extra_env({"CPA_BATCH_IMPORT_SIZE": ok})["CPA_BATCH_IMPORT_SIZE"] == ok
    with pytest.raises(ValueError, match="too long"):
        filter_extra_env({"CPA_BATCH_IMPORT_SIZE": "x" * (EXTRA_ENV_VALUE_MAX_LEN + 1)})


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
    # Stale process env must lose to config.json (console source of truth).
    monkeypatch.setenv("EMAIL_PROVIDER", "cloudflare")
    (tmp_path / "config.json").write_text(
        json.dumps({"email_provider": "hotmail", "email_provider_strategy": "round_robin"}),
        encoding="utf-8",
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
    child_env = captured["kwargs"].get("env") or {}
    assert child_env.get("EMAIL_PROVIDER") == "hotmail"
    assert child_env.get("EMAIL_PROVIDER_STRATEGY") == "round_robin"
    assert child_env.get("NODE_SCORE") == "1"
    # Parent log handle closed after successful Popen (child keeps its dup).
    assert closed["n"] == 1
    meta = result["run"]["meta"]
    assert meta.get("process_group") is True
    assert meta.get("pgid") == 555


def test_start_register_sh_outlook_argv(tmp_path: Path, monkeypatch):
    """kind=register_sh + product=outlook reaches register.sh as argv
    [bash, register.sh, outlook, target, threads] — the control-plane hop
    that lets an Outlook run launch. The live gate (GROK_REGISTER_OUTLOOK_LIVE)
    remains an operator env, NOT set here; the provider short-circuits to
    error_kind="provider", which is the safe non-live reachability contract."""
    # register.sh must exist for start_run's register_sh-branch file check.
    (tmp_path / "register.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (tmp_path / "logs").mkdir()

    class FakeProc:
        pid = 556

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    captured: dict = {}

    class FakeFile:
        def close(self):
            pass

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        return FakeProc()

    monkeypatch.setattr("apps.control_api.runs.subprocess.Popen", fake_popen)
    monkeypatch.setattr("apps.control_api.runs.supervisor_lock_held", lambda: False)
    monkeypatch.setattr("apps.control_api.runs.time.sleep", lambda _s: None)
    monkeypatch.setattr("builtins.open", lambda *_a, **_k: FakeFile())
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(lambda pid: pid == 556),
    )
    result = start_run(
        tmp_path,
        StartRunRequest(
            kind="register_sh",
            product="outlook",
            mode="residential",
            target=3,
            threads=2,
            tag="batch_web",
            extra_env={},
        ),
    )
    assert result["ok"] is True
    argv = captured["argv"]
    assert argv[0] == "bash"
    assert Path(argv[1]).name == "register.sh"
    assert argv[2] == "outlook"
    assert argv[3] == "3"
    assert argv[4] == "2"  # threads is forwarded; register.sh forwards it to --threads
    assert result["run"]["meta"]["product"] == "outlook"


def test_register_sh_outlook_branch_maps_to_register_core_run(tmp_path, monkeypatch):
    """register.sh's `outlook` branch execs `python -m register_core run --provider
    outlook -n COUNT --threads N`, which resolves the provider via the registry and
    calls ``OutlookProvider.register_one``. We pin the safe non-live contract at
    the provider layer directly (in-process, no subprocess, no network):

      - live gate OFF (``GROK_REGISTER_OUTLOOK_LIVE`` unset) → the provider
        short-circuits to ``error_kind="provider"`` with the gate phrase BEFORE
        any browser/mailbox/network is touched, in ~0s, and writes no artifact.

    This is the control-flow reachability contract for a non-live harness: the
    argv wiring from register.sh is already pinned by
    ``test_start_register_sh_outlook_argv`` above; here we pin that the provider
    the registry hands back honors the gate. Spawning the full ``register_core
    run`` subprocess is avoided because the pipeline runs the shared node/egress
    probe (network, ~12s libcurl timeouts per dead node) before reaching the
    per-account gate — that probe is environment-dependent and not part of this
    contract."""
    # Live gate OFF (default). No proxy, no browser, no mailbox is ever opened.
    monkeypatch.delenv("GROK_REGISTER_OUTLOOK_LIVE", raising=False)
    # Isolate the outlook_auths dir so a gate-on bug can't write into the repo.
    monkeypatch.setenv("OUTLOOK_AUTHS_DIR", str(tmp_path / "outlook_auths"))

    import time

    from register_core.providers.registry import get_provider

    provider = get_provider("outlook")
    t0 = time.monotonic()
    result = provider.register_one()
    elapsed = time.monotonic() - t0

    # The provider gated off without a LIVE flag → a provider-kind failure.
    assert result.ok is False
    assert result.error_kind == "provider"
    # No browser/mailbox round-trip should occur on a gated-off short-circuit —
    # this guards against a future change that moves network work above the gate.
    assert elapsed < 5.0, f"gated short-circuit took {elapsed:.3f}s (network?)"
    assert result.error is not None
    assert "live gate" in result.error.lower() or "GROK_REGISTER_OUTLOOK_LIVE" in result.error, (
        result.error
    )
    # No outlook auth artifact should have been written (gate is OFF).
    auths_dir = tmp_path / "outlook_auths"
    assert not auths_dir.exists() or not list(auths_dir.glob("outlook-*.json")), (
        "gate OFF must not write an artifact"
    )


def test_start_run_extra_env_overrides_config(tmp_path: Path, monkeypatch):
    """Request extra_env wins over config.json for one-shot experiments."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "launch_batch_supervisor.sh").write_text(
        "#!/bin/bash\n", encoding="utf-8"
    )
    (tmp_path / "logs").mkdir()
    (tmp_path / "config.json").write_text(
        json.dumps({"email_provider": "hotmail"}),
        encoding="utf-8",
    )

    class FakeProc:
        pid = 556

        def poll(self):
            return None

    captured: dict = {}

    class FakeFile:
        def close(self):
            pass

    def fake_popen(argv, **kwargs):
        captured["env"] = kwargs.get("env") or {}
        return FakeProc()

    monkeypatch.setattr("apps.control_api.runs.subprocess.Popen", fake_popen)
    monkeypatch.setattr("apps.control_api.runs.supervisor_lock_held", lambda: False)
    monkeypatch.setattr("apps.control_api.runs.time.sleep", lambda _s: None)
    monkeypatch.setattr("builtins.open", lambda *_a, **_k: FakeFile())
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(lambda pid: pid == 556),
    )
    start_run(
        tmp_path,
        StartRunRequest(
            kind="grok_supervisor",
            target=10,
            tag="t",
            extra_env={"EMAIL_PROVIDER": "cloudflare"},
        ),
    )
    assert captured["env"].get("EMAIL_PROVIDER") == "cloudflare"


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
