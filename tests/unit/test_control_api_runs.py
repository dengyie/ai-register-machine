"""Runs / process registry tests."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest
from fastapi import HTTPException

from apps.control_api.process_registry import ProcessRegistry, stop_pid
from apps.control_api.runs import filter_extra_env, start_run, stop_run
from apps.control_api.schemas import StartRunRequest


def test_extra_env_reject_unknown():
    with pytest.raises(ValueError, match="not allowed"):
        filter_extra_env({"EVIL": "1"})


def test_extra_env_allowlist():
    assert filter_extra_env({"SKIP_CLASH_PREFLIGHT": "1"}) == {"SKIP_CLASH_PREFLIGHT": "1"}


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
    reg.register("r1", 424242, "register_sh", {})
    calls: list[tuple[int, int]] = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        raise ProcessLookupError

    monkeypatch.setattr("apps.control_api.process_registry.os.kill", fake_kill)
    monkeypatch.setattr(
        "apps.control_api.process_registry.ProcessRegistry.pid_alive",
        staticmethod(lambda pid: pid == 424242),
    )
    # First alive check true, after SIGTERM ProcessLookupError → terminated path via stop_pid
    # Re-implement simpler: patch stop_pid
    monkeypatch.setattr(
        "apps.control_api.runs.stop_pid",
        lambda pid, grace_sec=10.0: {"ok": True, "detail": "terminated", "pid": pid},
    )
    out = stop_run(tmp_path)
    assert out["ok"] is True
    assert out["pid"] == 424242
    assert ProcessRegistry(tmp_path).current() is None


def test_start_popen_argv(tmp_path: Path, monkeypatch):
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "launch_batch_supervisor.sh"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    (tmp_path / "logs").mkdir()

    class FakeProc:
        pid = 555

    captured = {}

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr("apps.control_api.runs.subprocess.Popen", fake_popen)
    monkeypatch.setattr("apps.control_api.runs.supervisor_lock_held", lambda: False)
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
