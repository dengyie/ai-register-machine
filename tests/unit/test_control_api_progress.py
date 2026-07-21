"""Progress / phase detection for control plane Runs UI."""

from __future__ import annotations

from pathlib import Path

from apps.control_api.progress import build_progress, detect_phase, _parse_counters


def test_detect_phase_otp_over_older_browser():
    text = "\n".join(
        [
            "browser_boot Chromium started",
            "填写注册表单 create_account",
            "waiting OTP mail_code extract_otp",
        ]
    )
    p = detect_phase(text)
    assert p["phase"] == "otp"
    assert "OTP" in p["title"]


def test_detect_phase_turnstile():
    p = detect_phase("Turnstile token_len=0 卡住 fail-fast")
    assert p["phase"] == "turnstile"


def test_detect_phase_idle_empty():
    p = detect_phase("")
    assert p["phase"] == "idle"


def test_parse_counters_supervisor_line():
    text = (
        "[supervisor] complete=575 consecutive_zero=0 sub=42 chunk=3 "
        "mode=ordinary accounts=120 + 84/615 gained_complete=2\n"
    )
    c = _parse_counters(text)
    assert c["complete"] == 575
    assert c["consecutive_zero"] == 0
    assert c["sub"] == 42
    assert c["chunk"] == 3
    assert c["mode"] == "ordinary"
    assert c["gained_last_sub"] == 2
    assert c["batch_gained"] == 84
    assert c["target"] == 615


def test_parse_counters_goal_and_baseline():
    text = "\n".join(
        [
            "pid=1 mode=ordinary target_new=684 threads=1",
            "baseline total=506 complete=506 accounts=628 goal_complete=1190",
            "[supervisor] 02:16:51Z complete=591 (+85/684) accounts=755 attempt=63",
            "[supervisor] sub=63 gained_complete=2 consecutive_zero=0",
        ]
    )
    c = _parse_counters(text)
    assert c["complete"] == 591
    assert c["goal_complete"] == 1190
    assert c["baseline_complete"] == 506
    assert c["target"] == 684
    assert c["target_new"] == 684
    assert c["batch_gained"] == 85
    assert c["remain"] == 1190 - 591
    assert c["batch_remain"] == 684 - 85


def test_build_progress_steps_active_mint(tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    sup = logs / "batch_dc1k_ns.supervisor.log"
    sup.write_text(
        "\n".join(
            [
                "[supervisor] baseline complete=10 goal_complete=1190 mode=ordinary",
                "[supervisor] sub=1 chunk=3 log=logs/batch.sub1_abc.log",
                "[supervisor] complete=12 consecutive_zero=0 sub=1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    worker = logs / "batch.sub1_abc.log"
    worker.write_text(
        "browser_boot ok\nregister form create_account\nmint token_ok wrote cpa_auths/xai-demo.json\n",
        encoding="utf-8",
    )
    prog = build_progress(tmp_path, sup_log=sup)
    assert prog["phase"] == "mint"
    assert prog["complete"] == 12
    assert prog["goal_complete"] == 1190
    assert prog["baseline_complete"] == 10
    assert prog["remain"] == 1190 - 12
    assert any(s["id"] == "mint" and s["state"] == "active" for s in prog["steps"])
    assert any(s["id"] == "browser_boot" and s["state"] == "done" for s in prog["steps"])
    assert prog["worker_log"] and "batch.sub1_abc.log" in prog["worker_log"]
    assert prog["timeline"]


def test_build_progress_stuck_on_zero(tmp_path: Path):
    logs = tmp_path / "logs"
    logs.mkdir()
    sup = logs / "x.supervisor.log"
    sup.write_text(
        "[supervisor] complete=100 consecutive_zero=5 sub=9 chunk=3 mode=ordinary\n",
        encoding="utf-8",
    )
    prog = build_progress(tmp_path, sup_log=sup)
    assert prog["stuck"] is True
    assert "连续" in prog["stuck_reason"]


def test_detect_phase_turnstile_from_summary_fatal():
    line = (
        'SUMMARY_JSON {"event":"register_cli_summary","exit":2,"reg_success":0,'
        '"remote_inject_ok":0,"fatal":true,"fatal_reason":"Turnstile 卡住 fail-fast: '
        'pre-submit wait 31s token_len=0"}'
    )
    p = detect_phase(line + "\n")
    assert p["phase"] == "turnstile"
    # must NOT classify as batch_end_inject due to remote_inject_ok key
    assert p["phase"] != "batch_end_inject"


def test_build_progress_prefers_active_sub_over_finished_summary(tmp_path: Path, monkeypatch):
    """Regression: mid-sub must not stick on previous SUMMARY_JSON / 子批汇总.

    Supervisor only writes `log=` after a sub exits. During the next sub the
    newest mtime worker log is the authority — not the stale log= path.
    """
    import time

    logs = tmp_path / "logs"
    logs.mkdir()
    tag = "batch_dc1k_ns_ordinary_562_20260721_123509"
    sup = logs / f"{tag}.supervisor.log"
    finished = logs / f"{tag}.sub226_20260722_053405.log"
    active = logs / f"{tag}.sub227_20260722_054209.log"

    finished.write_text(
        "\n".join(
            [
                "[05:40:01] [WM1] [cpa] wrote cpa_auths/xai-old@example.com.json mint_method=browser",
                "=== 完成: 注册成功 2, 注册失败 1 ===",
                'SUMMARY_JSON {"event":"register_cli_summary","exit":0,"reg_success":2,'
                '"fatal":false,"fatal_reason":"","product_ok":true}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # Ensure finished is older than active.
    older = time.time() - 120
    import os

    os.utime(finished, (older, older))

    active.write_text(
        "\n".join(
            [
                "[*] lock acquired",
                "[05:47:50] browser_boot Chromium started",
                "[05:48:07] [WM1] [cpa] cookie inject count=39",
                "[05:48:22] [WM1] [cpa] oauth poll: authorization_pending",
                "mint token_ok wrote cpa_auths/xai-live@example.com.json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(active, None)

    sup.write_text(
        "\n".join(
            [
                f"[supervisor] sub=226 exit=0 log=logs/{finished.name}",
                "=== 完成: 注册成功 2, 注册失败 1 ===",
                'SUMMARY_JSON {"event":"register_cli_summary","exit":0,"reg_success":2,'
                '"fatal":false,"fatal_reason":"","product_ok":true}',
                "[supervisor] sub=226 gained_complete=2 consecutive_zero=0",
                "[supervisor] 21:42:09Z complete=800 (+172/562) accounts=1090 attempt=226",
                # Next sub started — no exit / log= yet (live production pattern).
                "[supervisor] sub=227 chunk=3 mode=ordinary clash_rotate zero=0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    prog = build_progress(tmp_path, sup_log=sup)
    assert prog["worker_log"] and active.name in prog["worker_log"], prog.get("worker_log")
    assert prog["phase"] != "sub_summary", prog
    assert prog["phase"] == "mint", prog
    assert any(s["id"] == "mint" and s["state"] == "active" for s in prog["steps"])
    assert any(s["id"] == "sub_summary" and s["state"] == "pending" for s in prog["steps"])


def test_build_progress_prefers_active_sub_over_finished_summary(tmp_path: Path, monkeypatch):
    """Regression: mid-sub must not stick on previous SUMMARY_JSON / 子批汇总.

    Supervisor only writes `log=` after a sub exits. During the next sub the
    newest mtime worker log is the authority — not the stale log= path.
    """
    import time

    logs = tmp_path / "logs"
    logs.mkdir()
    tag = "batch_dc1k_ns_ordinary_562_20260721_123509"
    sup = logs / f"{tag}.supervisor.log"
    finished = logs / f"{tag}.sub226_20260722_053405.log"
    active = logs / f"{tag}.sub227_20260722_054209.log"

    finished.write_text(
        "\n".join(
            [
                "[05:40:01] [WM1] [cpa] wrote cpa_auths/xai-old@example.com.json mint_method=browser",
                "=== 完成: 注册成功 2, 注册失败 1 ===",
                'SUMMARY_JSON {"event":"register_cli_summary","exit":0,"reg_success":2,'
                '"fatal":false,"fatal_reason":"","product_ok":true}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # Ensure finished is older than active.
    older = time.time() - 120
    import os

    os.utime(finished, (older, older))

    active.write_text(
        "\n".join(
            [
                "[*] lock acquired",
                "[05:47:50] browser_boot Chromium started",
                "[05:48:07] [WM1] [cpa] cookie inject count=39",
                "[05:48:22] [WM1] [cpa] oauth poll: authorization_pending",
                "mint token_ok wrote cpa_auths/xai-live@example.com.json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(active, None)

    sup.write_text(
        "\n".join(
            [
                f"[supervisor] sub=226 exit=0 log=logs/{finished.name}",
                "=== 完成: 注册成功 2, 注册失败 1 ===",
                'SUMMARY_JSON {"event":"register_cli_summary","exit":0,"reg_success":2,'
                '"fatal":false,"fatal_reason":"","product_ok":true}',
                "[supervisor] sub=226 gained_complete=2 consecutive_zero=0",
                "[supervisor] 21:42:09Z complete=800 (+172/562) accounts=1090 attempt=226",
                # Next sub started — no exit / log= yet (live production pattern).
                "[supervisor] sub=227 chunk=3 mode=ordinary clash_rotate zero=0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    prog = build_progress(tmp_path, sup_log=sup)
    assert prog["worker_log"] and active.name in prog["worker_log"], prog.get("worker_log")
    assert prog["phase"] != "sub_summary", prog
    assert prog["phase"] == "mint", prog
    assert any(s["id"] == "mint" and s["state"] == "active" for s in prog["steps"])
    assert any(s["id"] == "sub_summary" and s["state"] == "pending" for s in prog["steps"])
