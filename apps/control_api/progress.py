"""Parse supervisor / register_cli logs into human-readable run progress."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Avoid matching gained_complete=N (must not start mid-token).
_COMPLETE_RE = re.compile(r"(?<![A-Za-z0-9_])complete=(\d+)")
_ZERO_RE = re.compile(r"consecutive_zero=(\d+)")
_ZERO_SHORT_RE = re.compile(r"\bzero=(\d+)")
_SUB_RE = re.compile(r"sub=(\d+)")
_CHUNK_RE = re.compile(r"chunk=(\d+)")
_ATTEMPT_RE = re.compile(r"attempt=(\d+)")
_GAINED_RE = re.compile(r"gained_complete=(\d+)")
_PLUS_RE = re.compile(r"\+\s*(\d+)/(\d+)")
_ACCOUNTS_RE = re.compile(r"accounts=(\d+)")
_EXIT_RE = re.compile(r"exit=(\d+)")
_MODE_RE = re.compile(r"mode=(ordinary|residential)")
_GOAL_RE = re.compile(r"goal_complete=(\d+)")
_TARGET_NEW_RE = re.compile(r"target_new=(\d+)")
_BASELINE_RE = re.compile(
    r"baseline\s+(?:total=(\d+)\s+)?complete=(\d+)(?:\s+accounts=(\d+))?(?:\s+goal_complete=(\d+))?"
)
_SUB_LOG_RE = re.compile(r"log=(logs/\S+\.log)")
_SUMMARY_RE = re.compile(r"SUMMARY_JSON\s+(\{.*\})\s*$")
_WROTE_RE = re.compile(r"wrote\s+(\S+xai-\S+\.json)", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")

# Ordered from late → early so first match on reversed lines wins.
_PHASE_RULES: list[tuple[str, str, str]] = [
    # Batch-end inject only — do NOT match SUMMARY_JSON keys like remote_inject_ok.
    (
        r"batch-end import|batch_end_inject|CPA import start|tebi注入开始|\[batch-end\]",
        "batch_end_inject",
        "批末 CPA 注入 / 归档",
    ),
    (
        r"supervisor done|hit_target|goal reached|goal_complete reached",
        "done",
        "批次结束",
    ),
    (
        r"SUMMARY_JSON|=== 完成",
        "sub_summary",
        "子批汇总（reg/mint 统计）",
    ),
    (
        r"mint.*ok|token_ok|CPA token|access_token|refresh_token|browser mint|mint_success|"
        r"open device url|oauth poll|cookie inject|mint_method|\[cpa\].*device",
        "mint",
        "Mint：写 access+refresh 到 cpa_auths",
    ),
    (
        r"OTP|验证码|mail_code|extract_otp|邮箱验证",
        "otp",
        "邮箱 OTP 验证",
    ),
    (
        r"Turnstile|cf-turnstile|token_len=0|卡住 fail-fast|pre-submit",
        "turnstile",
        "Cloudflare Turnstile 人机验证",
    ),
    (
        r"完成注册|sign-up|signup|create_account|profile|givenName|familyName|您正在登录",
        "register_form",
        "填写/提交注册表单",
    ),
    (
        r"browser_boot|Chromium|xvfb|create_browser|ERR_CONNECTION",
        "browser_boot",
        "启动浏览器 / 代理连通",
    ),
    (
        r"clash_preflight|preflight|probe_clash|force_clash|rotate|switch.*node|NODE_SCORE",
        "node_rotate",
        "节点测活 / 换路 / 打分",
    ),
    (
        r"\[supervisor\].*chunk=|sub=\d+ chunk=",
        "sub_start",
        "启动子批 register_cli",
    ),
    (
        r"baseline|goal_complete|CPA_PROBE_CHAT",
        "batch_start",
        "批次启动 / 基线统计",
    ),
]


def _tail_text(path: Path | None, max_bytes: int = 120_000) -> str:
    if path is None or not path.is_file():
        return ""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, 2)
            data = f.read()
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _head_text(path: Path | None, max_bytes: int = 16_384) -> str:
    """Read start of supervisor log (baseline/goal_complete live only in header)."""
    if path is None or not path.is_file():
        return ""
    try:
        with path.open("rb") as f:
            data = f.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _run_tag_from_sup(sup_log: Path | None, sup_text: str) -> str | None:
    """Derive batch run tag so we only consider this run's sub logs.

    Supervisor files are `{tag}.supervisor.log`; sub logs are `{tag}.subN_TS.log`.
    """
    if sup_log is not None:
        name = sup_log.name
        if name.endswith(".supervisor.log"):
            return name[: -len(".supervisor.log")]
    for line in reversed(sup_text.splitlines()):
        m = _SUB_LOG_RE.search(line)
        if not m:
            continue
        leaf = Path(m.group(1)).name
        m2 = re.match(r"(.+)\.sub\d+", leaf)
        if m2:
            return m2.group(1)
    return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _latest_sub_log(
    root: Path, sup_text: str, *, sup_log: Path | None = None
) -> Path | None:
    """Pick the worker log for the *currently running* sub-batch.

    Supervisor only emits `log=` **after** a sub exits. Preferring that path
    during the next sub pins the UI on the finished SUMMARY_JSON (子批汇总)
    while the live sub keeps writing a newer `*.subN_*.log`. Always prefer the
    newest mtime sub log for this run tag; fall back to explicit log= only when
    no newer candidate exists.
    """
    logs = root / "logs"
    if not logs.is_dir():
        return None

    tag = _run_tag_from_sup(sup_log, sup_text)
    cands: list[Path] = []
    if tag:
        cands = list(logs.glob(f"{tag}.sub*.log"))
    if not cands:
        # Cross-run fallback (smoke / ad-hoc): any sub log by mtime.
        cands = list(logs.glob("*.sub*_*.log")) or list(logs.glob("*.sub*.log"))
    # De-dupe by resolved path.
    uniq: dict[str, Path] = {}
    for p in cands:
        try:
            uniq[str(p.resolve())] = p
        except OSError:
            uniq[str(p)] = p
    cands = list(uniq.values())
    if not cands:
        for line in reversed(sup_text.splitlines()):
            m = _SUB_LOG_RE.search(line)
            if m:
                p = root / m.group(1)
                if p.is_file():
                    return p
        return None

    newest = max(cands, key=_mtime)

    # If supervisor names an explicit log= that is still the newest (or only)
    # finished artifact, keep it — otherwise the in-flight newer file wins.
    for line in reversed(sup_text.splitlines()):
        m = _SUB_LOG_RE.search(line)
        if not m:
            continue
        p = root / m.group(1)
        if p.is_file() and _mtime(p) >= _mtime(newest) - 0.001:
            return p
        break
    return newest


def detect_phase(text: str) -> dict[str, str]:
    """Infer current phase from the most recent matching log lines."""
    if not text:
        return {
            "phase": "idle",
            "title": "空闲",
            "detail": "无活动日志",
        }
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # Prefer structured fatal_reason inside recent SUMMARY_JSON (more precise than
    # matching the whole JSON blob as sub_summary only).
    for line in reversed(lines[-80:]):
        if "SUMMARY_JSON" not in line or "fatal" not in line:
            continue
        if re.search(r'"fatal"\s*:\s*true', line) and re.search(
            r"Turnstile|token_len=0|pre-submit", line, re.I
        ):
            detail = line.strip()
            if len(detail) > 220:
                detail = detail[:217] + "…"
            return {
                "phase": "turnstile",
                "title": "Cloudflare Turnstile 人机验证",
                "detail": detail,
            }
    for line in reversed(lines[-400:]):
        for pattern, phase, title in _PHASE_RULES:
            if re.search(pattern, line, re.I):
                detail = line.strip()
                if len(detail) > 220:
                    detail = detail[:217] + "…"
                return {"phase": phase, "title": title, "detail": detail}
    last = lines[-1].strip() if lines else ""
    return {
        "phase": "running",
        "title": "运行中",
        "detail": last[:220] if last else "有日志但未识别步骤",
    }


def _parse_summary_json(text: str) -> dict[str, Any] | None:
    for line in reversed(text.splitlines()):
        if "SUMMARY_JSON" not in line:
            continue
        m = _SUMMARY_RE.search(line.strip())
        if not m:
            # tolerate "SUMMARY_JSON {...}" mid-line
            idx = line.find("{")
            if idx < 0:
                continue
            try:
                return json.loads(line[idx:])
            except Exception:
                continue
        try:
            return json.loads(m.group(1))
        except Exception:
            continue
    return None


def _parse_counters(text: str) -> dict[str, Any]:
    complete = None
    zero = None
    sub = None
    chunk = None
    attempt = None
    gained = None
    target = None
    gained_total = None
    accounts = None
    mode = None
    exit_code = None
    goal_complete = None
    baseline_complete = None
    target_new = None
    for line in reversed(text.splitlines()):
        if complete is None:
            m = _COMPLETE_RE.search(line)
            if m and "[supervisor]" in line:
                complete = int(m.group(1))
        if zero is None and "[supervisor]" in line:
            m2 = _ZERO_RE.search(line)
            if m2:
                zero = int(m2.group(1))
            else:
                m3 = _ZERO_SHORT_RE.search(line)
                if m3:
                    zero = int(m3.group(1))
        if sub is None:
            m = _SUB_RE.search(line)
            if m and "[supervisor]" in line:
                sub = int(m.group(1))
        if chunk is None:
            m = _CHUNK_RE.search(line)
            if m:
                chunk = int(m.group(1))
        if attempt is None:
            m = _ATTEMPT_RE.search(line)
            if m and "[supervisor]" in line:
                attempt = int(m.group(1))
        if gained is None:
            m = _GAINED_RE.search(line)
            if m:
                gained = int(m.group(1))
        if target is None or gained_total is None:
            m = _PLUS_RE.search(line)
            if m:
                gained_total = int(m.group(1))
                target = int(m.group(2))
        if accounts is None:
            m = _ACCOUNTS_RE.search(line)
            if m and "[supervisor]" in line:
                accounts = int(m.group(1))
        if mode is None:
            m = _MODE_RE.search(line)
            if m:
                mode = m.group(1)
        if exit_code is None and "sub=" in line and "exit=" in line:
            m = _EXIT_RE.search(line)
            if m:
                exit_code = int(m.group(1))
        if goal_complete is None:
            m = _GOAL_RE.search(line)
            if m:
                goal_complete = int(m.group(1))
        if target_new is None:
            m = _TARGET_NEW_RE.search(line)
            if m:
                target_new = int(m.group(1))
        if baseline_complete is None:
            m = _BASELINE_RE.search(line)
            if m:
                baseline_complete = int(m.group(2))
                if goal_complete is None and m.group(4):
                    goal_complete = int(m.group(4))
        if all(
            x is not None
            for x in (complete, zero, sub, chunk, accounts, goal_complete, target)
        ):
            break
    # Prefer explicit target_new when +N/M not yet seen; keep +N/M as batch target.
    if target is None and target_new is not None:
        target = target_new
    remain = None
    if isinstance(complete, int) and isinstance(goal_complete, int):
        remain = max(0, goal_complete - complete)
    batch_remain = None
    if isinstance(gained_total, int) and isinstance(target, int):
        batch_remain = max(0, target - gained_total)
    elif isinstance(target, int) and gained_total is None:
        batch_remain = target
    return {
        "complete": complete,
        "consecutive_zero": zero,
        "sub": sub,
        "chunk": chunk,
        "attempt": attempt if attempt is not None else sub,
        "gained_last_sub": gained,
        "batch_gained": gained_total,
        "target": target,
        "target_new": target_new if target_new is not None else target,
        "goal_complete": goal_complete,
        "baseline_complete": baseline_complete,
        "remain": remain,
        "batch_remain": batch_remain,
        "accounts": accounts,
        "mode": mode,
        "last_sub_exit": exit_code,
    }


def build_timeline(sup_text: str, sub_text: str, *, limit: int = 24) -> list[dict[str, str]]:
    """Build a short human timeline from recent supervisor + sub lines."""
    interesting = re.compile(
        r"\[supervisor\]|SUMMARY_JSON|=== 完成|Fatal|FAIL-FAST|Turnstile|"
        r"注册成功|token_ok|wrote |OTP|mint|preflight|clash|batch-end|"
        r"gained_complete|browser_boot|FATAL",
        re.I,
    )
    items: list[dict[str, str]] = []
    for src, text in (("supervisor", sup_text), ("worker", sub_text)):
        for line in text.splitlines():
            if not interesting.search(line):
                continue
            phase = detect_phase(line)
            items.append(
                {
                    "source": src,
                    "phase": phase["phase"],
                    "title": phase["title"],
                    "line": line.strip()[:300],
                }
            )
    return items[-limit:]


def build_progress(root: Path, *, sup_log: Path | None) -> dict[str, Any]:
    """Full progress payload for UI."""
    sup_text = _tail_text(sup_log)
    # Header has goal_complete / baseline / target_new; tail has live counters.
    # Merge so reversed-line parse still prefers latest complete= while
    # still seeing one-shot baseline fields from the start of the run.
    head_text = _head_text(sup_log)
    counters = _parse_counters((head_text + "\n" + sup_text).strip() if head_text else sup_text)
    sub_path = _latest_sub_log(root, sup_text, sup_log=sup_log)
    sub_text = _tail_text(sub_path)
    # Phase must reflect the live worker. Supervisor tail often ends with the
    # previous sub's SUMMARY_JSON (copied on exit) plus `sub=N chunk=` start —
    # those late rules (sub_summary) would otherwise pin the UI on 子批汇总 for
    # the entire next sub. Prefer worker-only when it has content; fall back to
    # combined only when worker is empty / missing.
    if sub_text.strip():
        phase = detect_phase(sub_text)
        # If worker is mid-flight but only has boot noise, allow supervisor
        # batch-level phases (sub_start / batch_start / done / inject) when
        # worker has no stronger in-account phase.
        if phase["phase"] in ("idle", "running"):
            sup_phase = detect_phase(sup_text)
            if sup_phase["phase"] not in ("sub_summary", "idle"):
                phase = sup_phase
    else:
        combined = (sup_text + "\n" + sub_text).strip()
        phase = detect_phase(combined if combined else sup_text)
    # SUMMARY_JSON is per finished sub. Prefer the active worker's own summary;
    # supervisor copy is previous-sub history while a newer worker is mid-flight.
    worker_summary = _parse_summary_json(sub_text)
    sup_summary = _parse_summary_json(sup_text)
    summary = worker_summary or sup_summary

    # Stuck heuristics
    stuck = False
    stuck_reason = ""
    zero = counters.get("consecutive_zero")
    if isinstance(zero, int) and zero >= 4:
        stuck = True
        stuck_reason = f"连续 {zero} 个子批零产出"
    if phase["phase"] == "turnstile":
        stuck = True
        stuck_reason = stuck_reason or "Turnstile 卡住 / 无 token"
    # Only treat fatal from the current worker (or when we are still on
    # sub_summary of that finished worker). A previous sub's fatal in the
    # supervisor tail must not freeze the next live sub as stuck.
    fatal_src = worker_summary
    if fatal_src is None and phase["phase"] in ("sub_summary", "done", "batch_end_inject"):
        fatal_src = sup_summary
    if fatal_src and fatal_src.get("fatal"):
        stuck = True
        stuck_reason = str(fatal_src.get("fatal_reason") or "fatal")[:200]

    recent_writes: list[str] = []
    for line in (sub_text + "\n" + sup_text).splitlines():
        m = _WROTE_RE.search(line)
        if m:
            recent_writes.append(m.group(1))
    recent_writes = recent_writes[-5:]

    steps = [
        {
            "id": "batch_start",
            "title": "批次启动",
            "desc": "基线 complete / goal / flock",
        },
        {
            "id": "node_rotate",
            "title": "节点测活/换路",
            "desc": "Clash preflight 或 force next node",
        },
        {
            "id": "sub_start",
            "title": "子批启动",
            "desc": "xvfb + register_cli chunk",
        },
        {
            "id": "browser_boot",
            "title": "浏览器启动",
            "desc": "Chromium + 代理",
        },
        {
            "id": "register_form",
            "title": "注册表单",
            "desc": "邮箱/密码/资料提交",
        },
        {
            "id": "turnstile",
            "title": "Turnstile",
            "desc": "人机验证 token",
        },
        {
            "id": "otp",
            "title": "邮箱 OTP",
            "desc": "收信解码验证码",
        },
        {
            "id": "mint",
            "title": "Mint 落盘",
            "desc": "access+refresh → cpa_auths",
        },
        {
            "id": "sub_summary",
            "title": "子批汇总",
            "desc": "SUMMARY_JSON / gained",
        },
        {
            "id": "batch_end_inject",
            "title": "批末注入",
            "desc": "goal 达成后 CPA import",
        },
    ]
    phase_order = [s["id"] for s in steps]
    cur = phase["phase"]
    if cur in ("done", "idle", "running"):
        active_idx = -1 if cur in ("done", "idle") else 2
    else:
        active_idx = phase_order.index(cur) if cur in phase_order else 2
    for i, s in enumerate(steps):
        if cur in ("done",):
            s["state"] = "done"
        elif active_idx < 0:
            s["state"] = "pending"
        elif i < active_idx:
            s["state"] = "done"
        elif i == active_idx:
            s["state"] = "active"
        else:
            s["state"] = "pending"

    return {
        **counters,
        "phase": phase["phase"],
        "phase_title": phase["title"],
        "phase_detail": phase["detail"],
        "stuck": stuck,
        "stuck_reason": stuck_reason,
        "summary": summary,
        "steps": steps,
        "timeline": build_timeline(sup_text, sub_text),
        "supervisor_log": str(sup_log) if sup_log else None,
        "worker_log": str(sub_path) if sub_path else None,
        "recent_writes": recent_writes,
        "last_lines": (sup_text.splitlines()[-8:] if sup_text else []),
        "worker_last_lines": (sub_text.splitlines()[-12:] if sub_text else []),
    }
