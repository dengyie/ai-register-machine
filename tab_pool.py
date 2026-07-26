#!/usr/bin/env python3
"""TabPool — per-thread Chromium with proper lifecycle.

Interface:
    TabPool.init(options_factory) → save options factory (no browser yet)
    TabPool.get_tab()             → get/create current thread browser tab
    TabPool.clear_session()       → wipe cookies/storage; keep process warm
    TabPool.release_tab()         → quit current thread browser + drop registry
    TabPool.shutdown()            → quit all known browsers
    cleanup_orphan_drission_chromes() → kill leftover Drission Chromes (+ helpers)
    cleanup_orphan_xvfb()            → kill PPID=1 Xvfb with no children + stale tmp

Notes:
    - One Chromium per worker thread (cookie isolation).
    - Prefer clear_session() between accounts; release_tab() only on errors / GC.
    - _all_browsers is pruned on release to avoid zombie list growth.
    - Failed Chromium() boots often leave a live chrome *child of this python*
      (not yet PPID=1) plus Helper processes sharing autoPortData. only_ppid_init
      alone misses those; use include_self_children + kill_related_helpers.
    - Xvfb orphans accumulate from xvfb-run crashes; only kill when PPID=init
      and no live children remain (active displays are left alone).
"""

from __future__ import annotations

import glob
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]

# Serialize Chromium process boots across register TabPool + mint standalone.
# Concurrent auto_port / user-data-dir allocation races produce
# "The browser connection fails" and PPID=1 orphans under load.
_chromium_start_lock = threading.Lock()
_AUTOPORT_GLOB = "/tmp/DrissionPage/autoPortData/*"
# DrissionPage creates autoPortData/<port> BEFORE Chrome execs, so a sibling
# worker mid-boot owns a dir that no process references in `ps` yet. Callers pass
# tmp_dir_max_age_sec=0 ("clean everything now"); without this floor that would
# delete a live worker's profile dir out from under it. Never go below it.
_AUTOPORT_MIN_AGE_SEC = 30.0
_test_connect_patch_lock = threading.Lock()
_test_connect_patched = False


def chromium_start_lock() -> threading.Lock:
    """Process-wide lock held only while constructing Chromium(...)."""
    return _chromium_start_lock


def cdp_target_has_page(targets: Any) -> bool:
    """True when CDP /json list already contains a page/webview target."""
    if not isinstance(targets, list):
        return False
    for item in targets:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip().lower() in ("page", "webview"):
            return True
    return False


def ensure_cdp_page_target(ip: str, port: int | str, *, timeout: float = 2.0) -> bool:
    """Create an about:blank page when Chrome only exposes service_worker targets.

    Chrome for Testing 149 + --load-extension (turnstilePatch) often boots with
    only a chrome-extension service_worker in /json. DrissionPage 4.1.x
    test_connect() requires type in {page, webview}, so Chromium() times out
    even though DevTools is healthy. PUT /json/new?about:blank unblocks it.
    Returns True if a page target exists after this call.
    """
    import json
    import urllib.error
    import urllib.request

    host = (ip or "127.0.0.1").replace("localhost", "127.0.0.1").strip() or "127.0.0.1"
    port_s = str(port).strip()
    if not port_s.isdigit():
        return False
    base = f"http://{host}:{port_s}"
    timeout = max(0.5, float(timeout or 2.0))

    def _get_json(path: str) -> Any:
        req = urllib.request.Request(
            f"{base}{path}",
            headers={"Connection": "close"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace") or "null")

    def _put_new() -> bool:
        # Prefer PUT /json/new?about:blank (CFT 149). Some builds also accept POST.
        for method in ("PUT", "POST"):
            try:
                req = urllib.request.Request(
                    f"{base}/json/new?about:blank",
                    headers={"Connection": "close"},
                    method=method,
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                if body:
                    return True
            except Exception:
                continue
        return False

    try:
        targets = _get_json("/json")
    except Exception:
        return False
    if cdp_target_has_page(targets):
        return True
    # Browser is up (we got /json) but no page — force one.
    _put_new()
    try:
        targets = _get_json("/json")
    except Exception:
        return False
    return cdp_target_has_page(targets)


def patch_drission_test_connect() -> bool:
    """Replace DrissionPage test_connect so extension-only boots get a page target.

    Idempotent. Safe no-op when DrissionPage is absent (unit tests / CI stubs).

    Why replace (not wrap-then-retry): original waits the full
    browser_connect_timeout looking only for page/webview. With CFT149 +
    --load-extension, /json is healthy but only service_worker — original
    always burns the whole timeout. Our loop polls, creates about:blank via
    /json/new when needed, and returns as soon as a page exists.
    """
    global _test_connect_patched
    with _test_connect_patch_lock:
        if _test_connect_patched:
            return True
        try:
            from DrissionPage._functions import browser as dp_browser  # type: ignore
        except Exception:
            return False
        original = getattr(dp_browser, "test_connect", None)
        if original is None or getattr(original, "_grok_page_target_patch", False):
            _test_connect_patched = True
            return True

        def _patched_test_connect(ip, port):  # type: ignore[no-untyped-def]
            try:
                from DrissionPage._functions.settings import Settings as _S  # type: ignore

                timeout_s = float(getattr(_S, "browser_connect_timeout", 30) or 30)
            except Exception:
                timeout_s = 30.0
            timeout_s = max(5.0, min(120.0, timeout_s))
            end = time.perf_counter() + timeout_s
            while time.perf_counter() < end:
                try:
                    if ensure_cdp_page_target(ip, port, timeout=1.0):
                        return True
                except Exception:
                    pass
                time.sleep(0.2)
            return False

        _patched_test_connect._grok_page_target_patch = True  # type: ignore[attr-defined]
        _patched_test_connect._grok_page_target_original = original  # type: ignore[attr-defined]
        dp_browser.test_connect = _patched_test_connect  # type: ignore[assignment]
        _test_connect_patched = True
        return True


def display_available() -> bool:
    """True if headed Chromium is likely to connect without Xvfb missing.

    macOS/Windows: always True (no X11 DISPLAY required).
    Linux/other: require non-empty ``DISPLAY`` (e.g. real desktop or ``xvfb-run``).
    Bare ``--headless`` on servers leaves DISPLAY empty; auto headless→headed
    upgrades must refuse rather than spin on "The browser connection fails".
    """
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        return True
    return bool((os.environ.get("DISPLAY") or "").strip())


def is_drission_marker(cmd: str) -> bool:
    """True when cmdline looks like our Drission/register chrome (main or Helper)."""
    if not cmd:
        return False
    return ("autoPortData" in cmd) or ("DrissionPage" in cmd) or ("turnstilePatch" in cmd)


def is_drission_chrome_cmdline(cmd: str) -> bool:
    """True for Drission/register Chrome mains (not Helpers / unrelated Chrome)."""
    if not cmd or "Helper" in cmd:
        return False
    if "remote-debugging-port" not in cmd:
        return False
    return is_drission_marker(cmd)


def is_drission_related_cmdline(cmd: str) -> bool:
    """True for Drission mains *or* Helpers sharing autoPortData / turnstilePatch."""
    if not cmd:
        return False
    # Require a debugging port or explicit autoPortData path so we never match
    # a normal desktop Chrome with an unrelated profile.
    if "remote-debugging-port" not in cmd and "autoPortData" not in cmd:
        return False
    return is_drission_marker(cmd)


def parse_remote_debugging_port(cmd: str) -> str:
    if not cmd or "remote-debugging-port=" not in cmd:
        return ""
    try:
        return cmd.split("remote-debugging-port=", 1)[1].split(None, 1)[0].strip()
    except Exception:
        return ""


def parse_user_data_dir(cmd: str) -> str:
    if not cmd or "user-data-dir=" not in cmd:
        return ""
    try:
        raw = cmd.split("user-data-dir=", 1)[1]
        # Chromium may quote the path; take until whitespace if unquoted.
        if raw.startswith('"'):
            return raw[1:].split('"', 1)[0]
        if raw.startswith("'"):
            return raw[1:].split("'", 1)[0]
        return raw.split(None, 1)[0].strip()
    except Exception:
        return ""


def parse_ps_chrome_rows(ps_text: str) -> list[tuple[int, int, str]]:
    """Parse ``ps -ax -o pid=,ppid=,command=`` style lines → (pid, ppid, cmd)."""
    rows: list[tuple[int, int, str]] = []
    for line in (ps_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        parts = s.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        cmd = parts[2]
        if is_drission_chrome_cmdline(cmd):
            rows.append((pid, ppid, cmd))
    return rows


def parse_ps_drission_related_rows(ps_text: str) -> list[tuple[int, int, str]]:
    """Parse ps lines → Drission main + Helper rows (pid, ppid, cmd)."""
    rows: list[tuple[int, int, str]] = []
    for line in (ps_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        parts = s.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        cmd = parts[2]
        if is_drission_related_cmdline(cmd):
            rows.append((pid, ppid, cmd))
    return rows


def _ps_ax_pid_ppid_cmd() -> str:
    proc = subprocess.run(
        ["ps", "-ax", "-o", "pid=,ppid=,command="],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return proc.stdout or ""


def _term_then_kill(pid: int, *, term_grace_sec: float) -> bool:
    """SIGTERM then SIGKILL. Returns True if we sent at least one signal."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return False
    deadline = time.time() + max(0.1, float(term_grace_sec))
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return True


def kill_process_tree(pid: int, *, term_grace_sec: float = 1.0) -> bool:
    """Best-effort kill of pid and, when it is a group leader, its process group.

    Chrome helpers are often children of the main browser process, not a separate
    session. Prefer killpg when pgid==pid; always SIGKILL the main pid as well.
    """
    if pid is None or int(pid) <= 0:
        return False
    pid = int(pid)
    signaled = False
    try:
        pgid = int(os.getpgid(pid))
    except (ProcessLookupError, PermissionError, OSError):
        pgid = pid
    if pgid == pid:
        try:
            os.killpg(pgid, signal.SIGTERM)
            signaled = True
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if _term_then_kill(pid, term_grace_sec=term_grace_sec):
        signaled = True
    if pgid == pid:
        try:
            os.killpg(pgid, signal.SIGKILL)
            signaled = True
        except (ProcessLookupError, PermissionError, OSError):
            pass
    return signaled


def _family_pids_for_main(
    main_pid: int,
    main_cmd: str,
    related_rows: list[tuple[int, int, str]],
) -> list[int]:
    """Return main + Helper/child pids sharing debug port or user-data-dir."""
    port = parse_remote_debugging_port(main_cmd)
    udd = parse_user_data_dir(main_cmd)
    family = {main_pid}
    for pid, _ppid, cmd in related_rows:
        if pid == main_pid:
            continue
        if port and parse_remote_debugging_port(cmd) == port:
            family.add(pid)
            continue
        if udd and parse_user_data_dir(cmd) == udd:
            family.add(pid)
            continue
        # Descendant still under main (mid-boot helpers before flags settle).
        if _ppid_chain_contains(pid, main_pid, related_rows):
            family.add(pid)
    return sorted(family)


def _ppid_chain_contains(
    pid: int,
    ancestor: int,
    rows: list[tuple[int, int, str]],
    *,
    max_depth: int = 8,
) -> bool:
    by_pid = {p: pp for p, pp, _ in rows}
    # rows only has Drission-related; fall back is fine for shallow trees
    cur = pid
    for _ in range(max_depth):
        pp = by_pid.get(cur)
        if pp is None:
            return False
        if pp == ancestor:
            return True
        if pp in (0, 1):
            return False
        cur = pp
    return False


def cleanup_orphan_drission_chromes(
    *,
    log_callback: LogFn | None = None,
    protect_pids: set[int] | None = None,
    only_ppid_init: bool = True,
    include_self_children: bool = False,
    kill_related_helpers: bool = True,
    clean_tmp_dirs: bool = False,
    tmp_dir_max_age_sec: float = 300.0,
    dry_run: bool = False,
    term_grace_sec: float = 1.0,
) -> dict[str, Any]:
    """Kill leftover Drission Chromium families (main + Helpers) and stale dirs.

    Safety:
      - Only matches Drission/register Chrome (autoPortData / turnstilePatch /
        DrissionPage), never generic user Chrome profiles (e.g. coinbot).
      - Default only_ppid_init=True → only PPID in {0, 1} (orphans reparented to
        init). Live tracked children of a healthy register_cli are left alone
        unless include_self_children=True *and* they are not in protect_pids.
      - include_self_children=True: also kill untracked Drission mains whose
        PPID is this process (failed Chromium() boots that never registered).
      - kill_related_helpers=True: for each matched main, also signal Helpers
        sharing the same remote-debugging-port or user-data-dir.
      - Never signals protect_pids, os.getpid(), or this process's parent.
      - SIGTERM first, then SIGKILL after a short grace (process group when
        the main is a group leader).

    Returns dict: scanned, matched, killed, helpers_killed, protected_skipped,
    tmp_removed, errors, pids, dry_run.
    """
    log = log_callback or (lambda _m: None)
    init_ppids = {0, 1}
    protect = set(protect_pids or ())
    protect.add(os.getpid())
    try:
        parent = int(os.getppid())
        # Never treat init/launchd as a protect parent — that would skip every
        # PPID=1 orphan when this process itself is reparented to init.
        if parent not in init_ppids:
            protect.add(parent)
    except Exception:
        pass

    result: dict[str, Any] = {
        "scanned": 0,
        "matched": 0,
        "killed": 0,
        "helpers_killed": 0,
        "protected_skipped": 0,
        "tmp_removed": 0,
        "errors": [],
        "pids": [],
        "dry_run": dry_run,
    }

    try:
        ps_text = _ps_ax_pid_ppid_cmd()
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"ps failed: {e}")
        log(f"[browser] orphan cleanup: ps failed: {e}")
        return result

    main_rows = parse_ps_chrome_rows(ps_text)
    related_rows = (
        parse_ps_drission_related_rows(ps_text) if kill_related_helpers else main_rows
    )
    result["scanned"] = len(main_rows)
    self_pid = os.getpid()

    # Ports / user-data-dirs still owned by explicitly protected live browsers
    # (tracked TabPool pids). Do NOT key off ppid∈protect: protect always
    # includes os.getpid(), which would mark every self-child as protected and
    # defeat include_self_children.
    protect_ports: set[str] = set()
    protect_udds: set[str] = set()
    for pid, _ppid, cmd in main_rows:
        if pid not in protect:
            continue
        port = parse_remote_debugging_port(cmd)
        udd = parse_user_data_dir(cmd)
        if port:
            protect_ports.add(port)
        if udd:
            protect_udds.add(udd)

    targets: list[tuple[int, int, str]] = []
    for pid, ppid, cmd in main_rows:
        is_init_orphan = ppid in init_ppids
        is_self_child = ppid == self_pid
        # Selection:
        #   only_ppid_init=False → all Drission mains (still honor protect_*)
        #   only_ppid_init=True  → PPID init orphans
        #   + include_self_children → also untracked children of this process
        if only_ppid_init:
            if not is_init_orphan and not (include_self_children and is_self_child):
                continue

        result["matched"] += 1
        if pid in protect:
            result["protected_skipped"] += 1
            continue
        # Untracked self-child is killable only when include_self_children.
        if is_self_child and not include_self_children:
            result["protected_skipped"] += 1
            continue
        # Child of a protected non-init parent (e.g. sibling service) — skip,
        # unless this is our own untracked failed-boot child.
        if (
            ppid in protect
            and ppid not in init_ppids
            and not (include_self_children and is_self_child)
        ):
            result["protected_skipped"] += 1
            continue

        port = parse_remote_debugging_port(cmd)
        udd = parse_user_data_dir(cmd)
        if port and port in protect_ports:
            result["protected_skipped"] += 1
            continue
        if udd and udd in protect_udds:
            result["protected_skipped"] += 1
            continue
        targets.append((pid, ppid, cmd))

    for pid, ppid, cmd in targets:
        port = parse_remote_debugging_port(cmd) or "?"
        family = (
            _family_pids_for_main(pid, cmd, related_rows)
            if kill_related_helpers
            else [pid]
        )
        # Never signal protected pids even if they share a port (shouldn't).
        family = [p for p in family if p not in protect]
        if dry_run:
            log(
                f"[browser] orphan would kill pid={pid} ppid={ppid} port={port} "
                f"family={family}"
            )
            result["pids"].append(pid)
            continue

        killed_main = False
        helpers = 0
        for fpid in family:
            if fpid == pid:
                if kill_process_tree(fpid, term_grace_sec=term_grace_sec):
                    killed_main = True
            else:
                if _term_then_kill(fpid, term_grace_sec=term_grace_sec):
                    helpers += 1
        if killed_main or helpers:
            # One family kill per main (or helpers-only when main already gone).
            result["killed"] += 1
            result["helpers_killed"] += helpers
            result["pids"].append(pid)
            log(
                f"[browser] killed orphan Drission Chrome pid={pid} ppid={ppid} "
                f"port={port} helpers={helpers}"
            )
        else:
            result["errors"].append(f"pid={pid}: signal failed")

    if clean_tmp_dirs:
        result["tmp_removed"] = _cleanup_stale_autoport_dirs(
            ps_text=ps_text,
            protect_udds=protect_udds,
            log=log,
            dry_run=dry_run,
            max_age_sec=tmp_dir_max_age_sec,
            errors=result["errors"],
        )

    if (
        result["matched"]
        or result["killed"]
        or result["helpers_killed"]
        or result["tmp_removed"]
    ):
        log(
            f"[browser] orphan cleanup: matched={result['matched']} "
            f"killed={result['killed']} helpers={result['helpers_killed']} "
            f"skipped={result['protected_skipped']} tmp_removed={result['tmp_removed']}"
        )
    return result


def _cleanup_stale_autoport_dirs(
    *,
    ps_text: str,
    protect_udds: set[str],
    log: LogFn,
    dry_run: bool,
    max_age_sec: float,
    errors: list[str],
) -> int:
    """Remove /tmp/DrissionPage/autoPortData/* dirs not referenced by live processes."""
    removed = 0
    now = time.time()
    # Floor, not a default: callers ask for 0 and must still not race a sibling
    # worker whose autoPortData dir exists but whose Chrome has not execed yet.
    max_age = max(_AUTOPORT_MIN_AGE_SEC, float(max_age_sec))
    try:
        paths = glob.glob(_AUTOPORT_GLOB)
    except Exception as e:  # noqa: BLE001
        errors.append(f"autoport glob: {e}")
        return 0
    for path_s in paths:
        p = Path(path_s)
        if not p.is_dir():
            continue
        try:
            # Resolve for comparison with cmdline paths.
            resolved = str(p.resolve()) if p.exists() else path_s
        except OSError:
            resolved = path_s
        if path_s in protect_udds or resolved in protect_udds:
            continue
        # Any live process still referencing this dir (main or Helper).
        if path_s in ps_text or resolved in ps_text:
            continue
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        if age < max_age:
            continue
        if dry_run:
            log(f"[browser] would remove stale autoPortData {path_s} age={int(age)}s")
            removed += 1
            continue
        try:
            shutil.rmtree(path_s, ignore_errors=True)
            removed += 1
            log(f"[browser] removed stale autoPortData {path_s} age={int(age)}s")
        except Exception as e:  # noqa: BLE001
            errors.append(f"autoport {path_s}: {e}")
    return removed


def is_xvfb_cmdline(cmd: str) -> bool:
    """True for Xvfb display servers (not xvfb-run shell wrappers)."""
    if not cmd:
        return False
    s = cmd.strip()
    # argv0 is Xvfb or .../Xvfb; ignore xvfb-run (shell helper still live).
    first = s.split(None, 1)[0]
    base = first.rsplit("/", 1)[-1]
    return base == "Xvfb"


def parse_ps_xvfb_rows(ps_text: str) -> list[tuple[int, int, str]]:
    """Parse ``ps -ax -o pid=,ppid=,command=`` → Xvfb (pid, ppid, cmd)."""
    rows: list[tuple[int, int, str]] = []
    for line in (ps_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        parts = s.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        cmd = parts[2]
        if is_xvfb_cmdline(cmd):
            rows.append((pid, ppid, cmd))
    return rows


def _pids_with_ppid(ps_text: str, parent_pid: int) -> list[int]:
    """Return child pids whose ppid equals parent_pid."""
    kids: list[int] = []
    for line in (ps_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        parts = s.split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        if ppid == parent_pid:
            kids.append(pid)
    return kids


def cleanup_orphan_xvfb(
    *,
    log_callback: LogFn | None = None,
    protect_pids: set[int] | None = None,
    only_ppid_init: bool = True,
    require_no_children: bool = True,
    dry_run: bool = False,
    term_grace_sec: float = 1.0,
    clean_tmp_dirs: bool = True,
    tmp_dir_max_age_sec: float = 3600.0,
) -> dict[str, Any]:
    """Kill orphan Xvfb servers left by crashed xvfb-run / register sessions.

    Safety:
      - Only matches real Xvfb binaries (not xvfb-run wrappers).
      - Default only_ppid_init=True → PPID in {0, 1} (orphans reparented to init).
      - Default require_no_children=True → skip Xvfb that still has live children
        (active display still serving chrome/python).
      - Never signals protect_pids / self / parent.
      - Optionally removes stale empty-ish /tmp/xvfb-run.* dirs older than max age.

    Returns dict: scanned, matched, killed, protected_skipped, tmp_removed, errors, pids.
    """
    log = log_callback or (lambda _m: None)
    protect = set(protect_pids or ())
    protect.add(os.getpid())
    try:
        protect.add(os.getppid())
    except Exception:
        pass

    result: dict[str, Any] = {
        "scanned": 0,
        "matched": 0,
        "killed": 0,
        "protected_skipped": 0,
        "child_skipped": 0,
        "tmp_removed": 0,
        "errors": [],
        "pids": [],
        "dry_run": dry_run,
    }

    try:
        proc = subprocess.run(
            ["ps", "-ax", "-o", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        ps_text = proc.stdout or ""
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"ps failed: {e}")
        log(f"[xvfb] orphan cleanup: ps failed: {e}")
        return result

    rows = parse_ps_xvfb_rows(ps_text)
    result["scanned"] = len(rows)
    init_ppids = {0, 1}

    for pid, ppid, cmd in rows:
        if only_ppid_init and ppid not in init_ppids:
            continue
        result["matched"] += 1
        if pid in protect or ppid in protect or ppid == os.getpid():
            result["protected_skipped"] += 1
            continue
        if require_no_children:
            kids = _pids_with_ppid(ps_text, pid)
            if kids:
                result["child_skipped"] += 1
                continue
        disp = ""
        try:
            # Xvfb :99 -screen ...
            toks = cmd.split()
            for t in toks[1:]:
                if t.startswith(":"):
                    disp = t
                    break
        except Exception:
            disp = "?"
        if dry_run:
            log(f"[xvfb] orphan would kill pid={pid} ppid={ppid} display={disp}")
            result["pids"].append(pid)
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as e:
            result["errors"].append(f"pid={pid}: {e}")
            continue
        except OSError as e:
            result["errors"].append(f"pid={pid}: {e}")
            continue

        deadline = time.time() + max(0.1, float(term_grace_sec))
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.05)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

        result["killed"] += 1
        result["pids"].append(pid)
        log(f"[xvfb] killed orphan Xvfb pid={pid} ppid={ppid} display={disp}")

    if clean_tmp_dirs and sys.platform.startswith("linux"):
        # Stale xvfb-run workdirs from crashed sessions (safe: age + empty-ish).
        try:
            import glob
            import shutil
            from pathlib import Path

            now = time.time()
            max_age = max(60.0, float(tmp_dir_max_age_sec))
            for path_s in glob.glob("/tmp/xvfb-run.*"):
                p = Path(path_s)
                if not p.is_dir():
                    continue
                try:
                    age = now - p.stat().st_mtime
                except OSError:
                    continue
                if age < max_age:
                    continue
                # Skip if any live process still references the path in cmdline.
                if path_s in ps_text:
                    continue
                if dry_run:
                    log(f"[xvfb] would remove stale tmp {path_s} age={int(age)}s")
                    result["tmp_removed"] += 1
                    continue
                try:
                    shutil.rmtree(path_s, ignore_errors=True)
                    result["tmp_removed"] += 1
                    log(f"[xvfb] removed stale tmp {path_s} age={int(age)}s")
                except Exception as e:  # noqa: BLE001
                    result["errors"].append(f"tmp {path_s}: {e}")
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"tmp cleanup: {e}")

    if result["matched"] or result["killed"] or result["tmp_removed"]:
        log(
            f"[xvfb] orphan cleanup: matched={result['matched']} "
            f"killed={result['killed']} child_skip={result['child_skipped']} "
            f"tmp_removed={result['tmp_removed']}"
        )
    return result


class TabPool:
    """Per-thread Chromium instance manager."""

    _options_factory = None
    _options_lock = threading.Lock()
    _thread_local = threading.local()
    _all_browsers: list[Any] = []
    _all_browsers_lock = threading.Lock()

    # ── public ──

    @classmethod
    def init(cls, browser_options_or_factory, log_callback=None):
        """Save options object or factory. Callable → fresh options each create."""
        with cls._options_lock:
            if callable(browser_options_or_factory):
                cls._options_factory = browser_options_or_factory
            else:
                # Shared options object: auto_port will NOT re-allocate.
                cls._options_factory = lambda: browser_options_or_factory
        if log_callback:
            log_callback("[*] TabPool 已初始化浏览器选项模板")

    @classmethod
    def _create_browser(cls):
        from DrissionPage import Chromium

        with cls._options_lock:
            factory = cls._options_factory
        if factory is None:
            return None
        options = factory()
        # Hold global start lock so mint standalone and other workers cannot
        # race auto_port / debugging-port allocation during Chromium() boot.
        # Patch Drission test_connect so CFT149+turnstilePatch service_worker-only
        # boots get an about:blank page target before connect timeout.
        patch_drission_test_connect()
        with _chromium_start_lock:
            browser = Chromium(options)
        with cls._all_browsers_lock:
            cls._all_browsers.append(browser)
        return browser

    @classmethod
    def _unregister(cls, browser) -> None:
        if browser is None:
            return
        with cls._all_browsers_lock:
            try:
                cls._all_browsers = [b for b in cls._all_browsers if b is not browser]
            except Exception:
                pass

    @classmethod
    def _try_kill(cls, browser) -> None:
        """Hard-kill the Chromium OS process tree if quit() failed.

        browser.process_id is the OS PID of the main browser. Kill the process
        group when it is a leader, then the pid itself — Helpers often survive
        a single SIGKILL of the main and keep holding autoPortData ports.
        """
        if browser is None:
            return
        pid = getattr(browser, "process_id", None)
        if pid is None or int(pid) <= 0:
            return
        try:
            kill_process_tree(int(pid), term_grace_sec=0.4)
        except Exception:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (OSError, PermissionError):
                pass

    @classmethod
    def get_tab(cls, url=None):
        """Return current thread tab; create Chromium on first use."""
        tab = getattr(cls._thread_local, "tab", None)
        if tab is not None:
            return tab
        browser = cls._create_browser()
        if browser is None:
            raise RuntimeError("TabPool not initialized — call init() first")
        tab_ids = browser.tab_ids
        if tab_ids:
            tab = browser.get_tab(tab_ids[0])
        else:
            tab = browser.new_tab()
        cls._thread_local.browser = browser
        cls._thread_local.tab = tab
        cls._thread_local.served = 0
        return tab

    @classmethod
    def sync_tab(cls):
        """Point thread-local tab at the browser's latest tab."""
        browser = getattr(cls._thread_local, "browser", None)
        if browser is None:
            return
        tabs = browser.tab_ids
        if tabs:
            cls._thread_local.tab = browser.get_tab(tabs[-1])

    @classmethod
    def clear_session(cls, log_callback=None) -> bool:
        """Clear cookies/storage and blank the page; keep Chromium process.

        Returns True if session was cleared on a live browser; False if no browser
        or if cookie wipe could not run at all (caller should hard-recycle).

        Hardening vs soft-reuse session bleed:
          - multi-tab close to a single keep tab
          - localStorage / sessionStorage / IndexedDB / ServiceWorker best-effort
          - cookie clear via set.cookies.clear, cookies.clear, or per-cookie remove
        """
        browser = getattr(cls._thread_local, "browser", None)
        tab = getattr(cls._thread_local, "tab", None)
        if browser is None:
            return False
        ok = True
        try:
            # Prefer a single clean tab first so later blank/storage hits the keep tab
            try:
                tabs = list(browser.tab_ids or [])
                if len(tabs) > 1:
                    keep = tabs[0]
                    for tid in tabs[1:]:
                        try:
                            browser.get_tab(tid).close()
                        except Exception:
                            pass
                    try:
                        tab = browser.get_tab(keep)
                        cls._thread_local.tab = tab
                    except Exception:
                        cls.sync_tab()
                        tab = getattr(cls._thread_local, "tab", None)
                elif tabs:
                    try:
                        tab = browser.get_tab(tabs[0])
                        cls._thread_local.tab = tab
                    except Exception:
                        cls.sync_tab()
                        tab = getattr(cls._thread_local, "tab", None)
            except Exception:
                cls.sync_tab()
                tab = getattr(cls._thread_local, "tab", None)

            if tab is not None:
                try:
                    tab.get("about:blank")
                except Exception:
                    pass
                for js in (
                    "try{localStorage.clear()}catch(e){}",
                    "try{sessionStorage.clear()}catch(e){}",
                    "try{indexedDB.databases&&indexedDB.databases().then(ds=>ds.forEach(d=>indexedDB.deleteDatabase(d.name)))}catch(e){}",
                    "try{navigator.serviceWorker&&navigator.serviceWorker.getRegistrations&&"
                    "navigator.serviceWorker.getRegistrations().then(rs=>rs.forEach(r=>r.unregister()))}catch(e){}",
                ):
                    try:
                        tab.run_js(js)
                    except Exception:
                        pass
            # Best-effort cookie wipe (API varies by DrissionPage version)
            cleared = False
            for target in (tab, browser):
                if target is None or cleared:
                    continue
                for attr_path in (
                    ("set", "cookies", "clear"),
                    ("cookies", "clear"),
                ):
                    try:
                        obj = target
                        for name in attr_path[:-1]:
                            obj = getattr(obj, name)
                        fn = getattr(obj, attr_path[-1])
                        fn()
                        cleared = True
                        break
                    except Exception:
                        continue
            if not cleared:
                try:
                    # Fallback: drop all cookies via CDP-ish helper if present
                    cks = browser.cookies()
                    if isinstance(cks, list):
                        removed_any = False
                        for c in cks:
                            try:
                                browser.set.cookies.remove(c)  # type: ignore[attr-defined]
                                removed_any = True
                            except Exception:
                                pass
                        # empty list counts as cleared; non-empty but none removed → fail
                        cleared = removed_any or len(cks) == 0
                except Exception:
                    cleared = False
            if not cleared:
                ok = False
                if log_callback:
                    log_callback("[!] clear_session cookie wipe incomplete — caller should hard recycle")
            if log_callback:
                served = int(getattr(cls._thread_local, "served", 0) or 0)
                log_callback(
                    f"[*] 浏览器会话已清理（复用进程, served={served}, cookies_ok={cleared}）"
                )
            return ok
        except Exception as exc:
            if log_callback:
                log_callback(f"[!] clear_session 失败: {exc}")
            return False

    @classmethod
    def mark_served(cls) -> int:
        n = int(getattr(cls._thread_local, "served", 0) or 0) + 1
        cls._thread_local.served = n
        return n

    @classmethod
    def served_count(cls) -> int:
        return int(getattr(cls._thread_local, "served", 0) or 0)

    @classmethod
    def release_tab(cls):
        """Quit current thread Chromium and unregister it.

        Always best-effort hard-kill the OS process tree after quit() so failed
        boots / hung CDP sessions cannot leave zombie mains or Helpers.
        """
        browser = getattr(cls._thread_local, "browser", None)
        if browser is not None:
            try:
                browser.quit(del_data=True)
            except TypeError:
                try:
                    browser.quit()
                except Exception:
                    pass
            except Exception:
                pass
            # quit() can report success while Helpers keep the debug port.
            cls._try_kill(browser)
            cls._unregister(browser)
        cls._thread_local.browser = None
        cls._thread_local.tab = None
        cls._thread_local.served = 0

    @classmethod
    def refresh_tab(cls):
        """Full recycle: quit + new browser."""
        cls.release_tab()
        return cls.get_tab()

    @classmethod
    def shutdown(cls):
        """Quit every browser we still track."""
        cls.release_tab()
        with cls._all_browsers_lock:
            browsers = list(cls._all_browsers)
            cls._all_browsers.clear()
        for b in browsers:
            try:
                b.quit(del_data=True)
            except TypeError:
                try:
                    b.quit()
                except Exception:
                    cls._try_kill(b)
            except Exception:
                cls._try_kill(b)

    @classmethod
    def live_count(cls) -> int:
        with cls._all_browsers_lock:
            return len(cls._all_browsers)

    @classmethod
    def get_browser(cls):
        return getattr(cls._thread_local, "browser", None)

    @classmethod
    def tracked_pids(cls) -> set[int]:
        """OS PIDs of Chromium instances currently tracked by TabPool."""
        out: set[int] = set()
        with cls._all_browsers_lock:
            browsers = list(cls._all_browsers)
        for b in browsers:
            pid = getattr(b, "process_id", None)
            if isinstance(pid, int) and pid > 0:
                out.add(pid)
        local = getattr(cls._thread_local, "browser", None)
        pid = getattr(local, "process_id", None) if local is not None else None
        if isinstance(pid, int) and pid > 0:
            out.add(pid)
        return out

    @classmethod
    def cleanup_orphans(cls, log_callback: LogFn | None = None, **kwargs: Any) -> dict[str, Any]:
        """Kill leftover Drission families; never touch tracked/live children.

        Defaults (for recycle/boot-fail paths):
          only_ppid_init=True, include_self_children=True, kill_related_helpers=True
        so failed Chromium() boots still attached to this python are reaped, not
        only PPID=1 orphans.
        """
        protect = set(kwargs.pop("protect_pids", None) or ())
        protect |= cls.tracked_pids()
        kwargs.setdefault("only_ppid_init", True)
        kwargs.setdefault("include_self_children", True)
        kwargs.setdefault("kill_related_helpers", True)
        return cleanup_orphan_drission_chromes(
            log_callback=log_callback,
            protect_pids=protect,
            **kwargs,
        )
