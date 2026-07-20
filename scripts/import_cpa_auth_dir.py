#!/usr/bin/env python3
"""Import a directory of xai-*.json into local cpa_auths + optional remote CPA.

Safety rules:
  1) **Access-first**: reuse a still-valid access_token; do NOT refresh unless
     access is expired/near-exp or models/chat return 401. Unconditional refresh
     rotates RT and permanently revokes the previous one — that is the bug that
     killed recoverable backups.
  2) When refresh *is* required, ``ensure_auth_tokens`` / ``refresh_auth_file``
     persist the new RT immediately and **re-read verify** before any probe.
  3) Probe models + chat; only chat_ok may inject (product gate).
  4) Never inject usage_exhausted / entitlement_denied / refresh-dead tokens.
  5) After *any* live probe/import attempt (ok or fail), stamp the source auth as
     tested/archived + append ledger so later runs skip used accounts.
  6) Process in small batches (default 5) with a pause between batches to avoid
     hammering Clash / token / chat endpoints.

Usage (project root):
  uv run python -u scripts/import_cpa_auth_dir.py \\
    --src /path/to/auth_dir \\
    --proxy http://127.0.0.1:7897

  # local only
  uv run python -u scripts/import_cpa_auth_dir.py --src ./incoming --no-remote

  # force token endpoint even when access still valid (ops only)
  uv run python -u scripts/import_cpa_auth_dir.py --src ./incoming --force-refresh

  # re-inject even if cpa_remote_injected stamp is set
  uv run python -u scripts/import_cpa_auth_dir.py --src ./incoming --force-reinject

  # batch size / pause (Clash-friendly; default 5 / 3s)
  uv run python -u scripts/import_cpa_auth_dir.py --src ./incoming --batch-size 5 --batch-pause 3
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cpa_export import apply_multi_remote_inject, evaluate_remote_inject_gate  # noqa: E402
from cpa_xai.probe import classify_chat_probe, probe_mini_response, probe_models  # noqa: E402
from cpa_xai.refresh import ensure_auth_tokens  # noqa: E402
from cpa_xai.schema import build_cpa_xai_auth  # noqa: E402
from cpa_xai.writer import patch_cpa_xai_auth, write_cpa_xai_auth  # noqa: E402

# On-disk product stamps + ledger. Auth files stay in cpa_auths (product count).
INJECTED_STAMP_KEY = "cpa_remote_injected"
INJECTED_AT_KEY = "cpa_remote_injected_at"
INJECTED_PATH_KEY = "cpa_remote_path"
TESTED_STAMP_KEY = "import_tested"
TESTED_AT_KEY = "import_tested_at"
TESTED_RESULT_KEY = "import_tested_result"
ARCHIVE_LEDGER_NAME = "cpa_import_archive.jsonl"
DEFAULT_ARCHIVE_DIR = "output/cpa_injected_archive"
DEFAULT_BATCH_SIZE = 5
DEFAULT_BATCH_PAUSE_SEC = 3.0


def _load_cfg(path: Path | None) -> dict:
    p = path or (_ROOT / "config.json")
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _config_bool(v, default=False):  # noqa: ANN001
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "on", "y"}:
        return True
    if s in {"0", "false", "no", "off", "n", ""}:
        return False
    return default


def _is_auth_expired_response(probe: dict | None) -> bool:
    """Heuristic: models/chat probe failed due to expired/invalid access token."""
    p = probe or {}
    status = p.get("status") or p.get("http_status") or 0
    try:
        status_i = int(status)
    except Exception:
        status_i = 0
    if status_i in {401, 403}:
        # 403 may also be entitlement — only treat as auth if body hints token.
        err = str(p.get("error") or p.get("body") or "").lower()
        if status_i == 401:
            return True
        if any(
            k in err
            for k in (
                "invalid_token",
                "token expired",
                "expired",
                "unauthorized",
                "access_token",
                "jwt",
            )
        ):
            return True
    err = str(p.get("error") or "").lower()
    return any(
        k in err
        for k in (
            "invalid_token",
            "token expired",
            "access token",
            "unauthorized",
            "jwt expired",
        )
    )


def _tokens_from_ensure(ref: dict) -> tuple[str, str, str, str]:
    access = str(ref.get("access_token") or "")
    refresh = str(ref.get("refresh_token") or "")
    email = str(ref.get("email") or "")
    id_token = str(ref.get("id_token") or "")
    return access, refresh, email, id_token


def _read_auth_obj(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _is_already_handled(path: Path) -> tuple[bool, dict, str]:
    """True when source was already injected or previously import-tested/archived."""
    data = _read_auth_obj(path)
    if data.get(INJECTED_STAMP_KEY) is True:
        return True, data, "already_cpa_remote_injected"
    if data.get("import_archived") is True and data.get("remote_live_ok") is True:
        return True, data, "already_cpa_remote_injected"
    # Any prior import attempt counts as used (ok or fail) — do not re-probe.
    if data.get(TESTED_STAMP_KEY) is True or data.get("import_archived") is True:
        return True, data, "already_import_tested"
    return False, data, ""


def _is_already_injected(path: Path) -> tuple[bool, dict]:
    """Compat: injected-only check."""
    handled, data, reason = _is_already_handled(path)
    if handled and reason == "already_cpa_remote_injected":
        return True, data
    if data.get(INJECTED_STAMP_KEY) is True:
        return True, data
    return False, data


def _append_archive_ledger(
    ledger_path: Path,
    *,
    email: str,
    src_path: Path,
    remote_path: str | None,
    live_ok: bool,
    inv_ok: bool | None,
    extra: dict | None = None,
) -> None:
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "email": (email or "").strip().lower(),
        "src": str(src_path),
        "file": src_path.name,
        "remote_path": remote_path or "",
        "remote_live_ok": bool(live_ok),
        "remote_inventory_ok": inv_ok,
        "archived": True,
    }
    if extra:
        for k, v in extra.items():
            if k not in rec and v is not None:
                rec[k] = v
    with ledger_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    try:
        os.chmod(ledger_path, 0o600)
    except OSError:
        pass


def _archive_copy(src_path: Path, archive_dir: Path) -> str | None:
    """Copy auth JSON into archive_dir (does not remove product file)."""
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / src_path.name
        shutil.copy2(src_path, dest)
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        return str(dest)
    except Exception:
        return None


def _stamp_import_result(
    src_path: Path,
    local_path: Path | None,
    *,
    archive_dir: Path,
    ledger_path: Path,
    email: str,
    result: dict,
) -> dict:
    """Stamp any import attempt (ok/fail) as tested+archived. Auth stays in cpa_auths.

    Used accounts must not re-enter the import queue next run.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stage = str(result.get("stage") or "")
    live_ok = bool(result.get("remote_live_ok"))
    injected = live_ok and stage == "inject" and bool(result.get("ok"))
    stamp = {
        TESTED_STAMP_KEY: True,
        TESTED_AT_KEY: now,
        TESTED_RESULT_KEY: {
            "ok": bool(result.get("ok")),
            "stage": stage,
            "error": str(result.get("error") or "")[:240],
            "token_source": result.get("token_source"),
            "chat_ok": result.get("chat_ok"),
            "remote_live_ok": result.get("remote_live_ok"),
        },
        "import_archived": True,
        "import_gate": result.get("import_gate")
        or stage
        or ("chat_ok" if result.get("chat_ok") else ""),
    }
    if injected:
        stamp[INJECTED_STAMP_KEY] = True
        stamp[INJECTED_AT_KEY] = now
        stamp[INJECTED_PATH_KEY] = str(result.get("remote_path") or "")
        stamp["remote_live_ok"] = True
        stamp["remote_inventory_ok"] = result.get("remote_inventory_ok")
        stamp["chat_ok"] = True
        stamp["usable"] = True
        stamp["import_gate"] = "chat_ok"
    elif result.get("chat_ok") is False:
        stamp["chat_ok"] = False
        stamp["usable"] = False
    if result.get("entitlement_denied") is not None:
        stamp["entitlement_denied"] = bool(result.get("entitlement_denied"))

    try:
        patch_cpa_xai_auth(src_path, stamp)
    except Exception as e:  # noqa: BLE001
        stamp["src_stamp_error"] = str(e)[:160]
    if local_path and Path(local_path).is_file() and Path(local_path).resolve() != src_path.resolve():
        try:
            patch_cpa_xai_auth(Path(local_path), stamp)
        except Exception as e:  # noqa: BLE001
            stamp["local_stamp_error"] = str(e)[:160]

    arch = _archive_copy(src_path, archive_dir)
    if arch:
        stamp["archive_copy"] = arch
    _append_archive_ledger(
        ledger_path,
        email=email or str(result.get("email") or ""),
        src_path=src_path,
        remote_path=str(result.get("remote_path") or ""),
        live_ok=live_ok,
        inv_ok=result.get("remote_inventory_ok"),
        extra={
            "archive_copy": arch,
            "injected": injected,
            "stage": stage,
            "ok": bool(result.get("ok")),
            "error": str(result.get("error") or "")[:240],
            "token_source": result.get("token_source"),
            "tested": True,
        },
    )
    return stamp


def _stamp_injected(
    src_path: Path,
    local_path: Path | None,
    *,
    remote_path: str | None,
    live_ok: bool,
    inv_ok: bool | None,
    archive_dir: Path,
    ledger_path: Path,
    email: str,
) -> dict:
    """Compat wrapper: successful live inject archive stamp."""
    return _stamp_import_result(
        src_path,
        local_path,
        archive_dir=archive_dir,
        ledger_path=ledger_path,
        email=email,
        result={
            "ok": True,
            "stage": "inject",
            "email": email,
            "remote_live_ok": live_ok,
            "remote_inventory_ok": inv_ok,
            "remote_path": remote_path or "",
            "chat_ok": True,
            "import_gate": "chat_ok",
        },
    )


def import_one(
    src_path: Path,
    *,
    out_dir: Path,
    cfg: dict,
    proxy: str | None,
    remote: bool,
    log,
    force_refresh: bool = False,
    force_reinject: bool = False,
    skip_injected: bool = True,
    archive_dir: Path | None = None,
    ledger_path: Path | None = None,
) -> dict:
    email_hint = src_path.name
    rec: dict = {
        "file": src_path.name,
        "ts": datetime.now(timezone.utc).isoformat(),
    }

    # 0) Skip already-handled accounts (injected OR previously tested/archived).
    if skip_injected and not force_reinject:
        already, prev, skip_reason = _is_already_handled(src_path)
        if already:
            was_injected = skip_reason == "already_cpa_remote_injected" or bool(
                prev.get(INJECTED_STAMP_KEY)
            )
            rec.update(
                {
                    "ok": True,
                    "stage": "skip_injected" if was_injected else "skip_tested",
                    "email": str(prev.get("email") or email_hint),
                    "skipped": True,
                    "skip_reason": skip_reason or "already_handled",
                    "remote_live_ok": True if was_injected else prev.get("remote_live_ok"),
                    "chat_ok": prev.get("chat_ok"),
                    "remote_path": prev.get(INJECTED_PATH_KEY) or prev.get("remote_path"),
                    "injected_at": prev.get(INJECTED_AT_KEY),
                    "tested_at": prev.get(TESTED_AT_KEY),
                    "tested_result": prev.get(TESTED_RESULT_KEY),
                }
            )
            return rec

    # 1) Access-first: only hit token endpoint when access is dead / forced.
    ref = ensure_auth_tokens(src_path, proxy=proxy, force_refresh=force_refresh)
    rec["email"] = ref.get("email") or email_hint
    rec["token_source"] = ref.get("source")
    rec["refresh_rotated"] = bool(ref.get("refresh_rotated"))
    rec["access_seconds_left"] = ref.get("access_seconds_left")
    if not ref.get("ok"):
        rec.update(
            {
                "ok": False,
                "stage": "refresh" if ref.get("source") == "refresh" else "tokens",
                "error": str(ref.get("error") or "")[:240],
                "status": ref.get("status"),
                "error_code": ref.get("error_code"),
            }
        )
        return rec

    access, refresh, email, id_token = _tokens_from_ensure(ref)
    if not access or not refresh:
        rec.update(
            {
                "ok": False,
                "stage": "tokens",
                "error": "missing access or refresh after ensure_auth_tokens",
            }
        )
        return rec

    # 2) Live usability probes with current access token.
    models = probe_models(access, proxy=proxy)
    # If access looked valid but upstream rejects auth → one forced refresh.
    if not models.get("has_grok_45") and _is_auth_expired_response(models) and not force_refresh:
        log(
            f"[import] {email or email_hint}: models auth-fail → force refresh once"
        )
        ref2 = ensure_auth_tokens(src_path, proxy=proxy, force_refresh=True)
        rec["token_source"] = f"{rec.get('token_source')}+retry_refresh"
        rec["refresh_rotated"] = bool(ref2.get("refresh_rotated")) or rec["refresh_rotated"]
        if not ref2.get("ok"):
            rec.update(
                {
                    "ok": False,
                    "stage": "refresh",
                    "error": str(ref2.get("error") or "retry refresh failed")[:240],
                    "status": ref2.get("status"),
                    "error_code": ref2.get("error_code"),
                }
            )
            return rec
        access, refresh, email, id_token = _tokens_from_ensure(ref2)
        models = probe_models(access, proxy=proxy)

    rec["models_has_grok_45"] = bool(models.get("has_grok_45"))
    if not models.get("has_grok_45"):
        rec.update(
            {
                "ok": False,
                "stage": "models",
                "chat_ok": False,
                "usable": False,
                "error": f"no grok-4.5 status={models.get('status')}",
            }
        )
        return rec

    ch = probe_mini_response(access, proxy=proxy)
    if not ch.get("ok") and _is_auth_expired_response(ch) and not force_refresh:
        # Access expired mid-flight (rare) — refresh once and re-probe chat only.
        log(f"[import] {email or email_hint}: chat auth-fail → force refresh once")
        ref3 = ensure_auth_tokens(src_path, proxy=proxy, force_refresh=True)
        rec["token_source"] = f"{rec.get('token_source')}+chat_retry_refresh"
        rec["refresh_rotated"] = bool(ref3.get("refresh_rotated")) or rec["refresh_rotated"]
        if not ref3.get("ok"):
            rec.update(
                {
                    "ok": False,
                    "stage": "refresh",
                    "error": str(ref3.get("error") or "chat-retry refresh failed")[:240],
                    "status": ref3.get("status"),
                    "error_code": ref3.get("error_code"),
                }
            )
            return rec
        access, refresh, email, id_token = _tokens_from_ensure(ref3)
        ch = probe_mini_response(access, proxy=proxy)

    cls = classify_chat_probe(ch)
    rec["chat_status"] = ch.get("status")
    rec["chat_reason"] = cls.get("reason")
    rec["chat_error_code"] = ch.get("error_code") or cls.get("error_code")
    if not ch.get("ok"):
        # still write local stamp for ops, but do not inject
        try:
            src_data = json.loads(src_path.read_text(encoding="utf-8"))
            # Prefer disk RT (post ensure) over in-memory in case of partial.
            disk_rt = str(src_data.get("refresh_token") or refresh)
            disk_at = str(src_data.get("access_token") or access)
            payload = build_cpa_xai_auth(
                email=email,
                access_token=disk_at,
                refresh_token=disk_rt,
                id_token=id_token or src_data.get("id_token") or "",
                sub=str(src_data.get("sub") or ref.get("sub") or ""),
                base_url=str(src_data.get("base_url") or ""),
                priority=int(cfg.get("cpa_auth_priority") or src_data.get("priority") or 1000),
            )
            local_path = write_cpa_xai_auth(out_dir, payload)
            gate_reason = str(cls.get("reason") or "chat_not_ok")
            patch_cpa_xai_auth(
                local_path,
                {
                    "chat_ok": False,
                    "usable": False,
                    "entitlement_denied": bool(cls.get("entitlement_denied")),
                    "chat_retryable": bool(cls.get("retryable")),
                    "fail_reason": gate_reason,
                    "import_gate": gate_reason,
                    "chat_error_code": rec.get("chat_error_code") or "",
                },
            )
            rec["path"] = str(local_path)
        except Exception as e:  # noqa: BLE001
            rec["local_write_error"] = str(e)[:160]
        rec.update(
            {
                "ok": False,
                "stage": "chat",
                "chat_ok": False,
                "usable": False,
                "error": (str(ch.get("error") or cls.get("reason") or "chat_failed"))[:240],
            }
        )
        return rec

    # 3) Write local CPA auth with chat_ok stamps — always from **disk** after ensure
    # so we never invent a stale RT that differs from the authority file.
    src_data = json.loads(src_path.read_text(encoding="utf-8"))
    disk_rt = str(src_data.get("refresh_token") or refresh)
    disk_at = str(src_data.get("access_token") or access)
    if disk_rt != refresh:
        # ensure refreshed but re-read diverged — trust disk, log.
        log(
            f"[import] WARN {email}: in-memory RT != disk after ensure; using disk"
        )
        refresh = disk_rt
    if disk_at != access:
        access = disk_at
    payload = build_cpa_xai_auth(
        email=email,
        access_token=access,
        refresh_token=refresh,
        id_token=id_token or src_data.get("id_token") or "",
        sub=str(src_data.get("sub") or ref.get("sub") or ""),
        base_url=str(src_data.get("base_url") or ""),
        priority=int(cfg.get("cpa_auth_priority") or src_data.get("priority") or 1000),
    )
    local_path = write_cpa_xai_auth(out_dir, payload)
    patch_cpa_xai_auth(
        local_path,
        {
            "chat_ok": True,
            "usable": True,
            "entitlement_denied": False,
            "chat_retryable": False,
            "import_gate": "chat_ok",
        },
    )
    # Keep source stamps aligned too (no token rewrite — tokens already on source).
    patch_cpa_xai_auth(
        src_path,
        {
            "chat_ok": True,
            "usable": True,
            "entitlement_denied": False,
            "chat_retryable": False,
            "import_gate": "chat_ok",
        },
    )

    result = {
        "ok": True,
        "path": str(local_path),
        "email": email,
        "chat_ok": True,
        "usable": True,
        "entitlement_denied": False,
        "import_gate": "chat_ok",
    }
    gate = evaluate_remote_inject_gate(result, cfg, auth_path=local_path)
    if not gate.get("allow"):
        rec.update(
            {
                "ok": False,
                "stage": "gate",
                "path": str(local_path),
                "chat_ok": True,
                "error": gate.get("reason"),
            }
        )
        return rec

    if not remote:
        rec.update(
            {
                "ok": True,
                "stage": "local",
                "path": str(local_path),
                "chat_ok": True,
                "usable": True,
                "import_gate": "chat_ok",
                "remote_skipped": True,
            }
        )
        return rec

    # 4) Remote inject under hard chat_ok gate.
    inj = apply_multi_remote_inject(result, cfg, log_callback=log)
    live_ok = inj.get("remote_live_ok")
    inv_ok = inj.get("remote_inventory_ok")
    rec.update(
        {
            "ok": bool(live_ok),
            "stage": "inject",
            "path": str(local_path),
            "chat_ok": True,
            "usable": True,
            "import_gate": "chat_ok",
            "remote_live_ok": live_ok,
            "remote_inventory_ok": inv_ok,
            "remote_path": inj.get("remote_path"),
            "remote_error": inj.get("remote_inject_error"),
        }
    )
    if not live_ok:
        rec["ok"] = False
        rec["error"] = (
            inj.get("remote_inject_error")
            or inj.get("remote_inject_skip_reason")
            or "live inject failed"
        )
    # Archive/tested stamp happens in main for every non-skip attempt.
    return rec


def _maybe_archive_attempt(
    rec: dict,
    src_path: Path,
    *,
    archive_dir: Path,
    ledger_path: Path,
) -> dict:
    """Stamp+ledger+copy for any real attempt (not skip_*). Mutates rec."""
    stage = str(rec.get("stage") or "")
    if stage.startswith("skip_") or rec.get("skipped"):
        return rec
    local = Path(rec["path"]) if rec.get("path") else None
    stamp_info = _stamp_import_result(
        src_path,
        local,
        archive_dir=archive_dir,
        ledger_path=ledger_path,
        email=str(rec.get("email") or ""),
        result=rec,
    )
    rec["archived"] = True
    rec["tested"] = True
    rec["archive_copy"] = stamp_info.get("archive_copy")
    if stamp_info.get(INJECTED_AT_KEY):
        rec[INJECTED_AT_KEY] = stamp_info.get(INJECTED_AT_KEY)
    rec[TESTED_AT_KEY] = stamp_info.get(TESTED_AT_KEY)
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", required=True, help="Directory of xai-*.json")
    ap.add_argument("--out-dir", default=str(_ROOT / "cpa_auths"))
    ap.add_argument("--config", default=str(_ROOT / "config.json"))
    ap.add_argument("--proxy", default="", help="Override proxy; default from config/env")
    ap.add_argument("--no-remote", action="store_true", help="Skip tebi remote inject")
    ap.add_argument(
        "--force-refresh",
        action="store_true",
        help="Always hit token endpoint (default: reuse valid access, no RT rotation)",
    )
    ap.add_argument(
        "--force-reinject",
        action="store_true",
        help="Re-inject even when cpa_remote_injected / import_tested stamp is set",
    )
    ap.add_argument(
        "--no-skip-injected",
        action="store_true",
        help="Do not skip already-handled stamps (same as --force-reinject for skip gate)",
    )
    ap.add_argument(
        "--archive-dir",
        default="",
        help=f"Copy tested/injected auths here (default: {_ROOT / DEFAULT_ARCHIVE_DIR})",
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Accounts per batch before pause (default {DEFAULT_BATCH_SIZE}; 0=no batching)",
    )
    ap.add_argument(
        "--batch-pause",
        type=float,
        default=DEFAULT_BATCH_PAUSE_SEC,
        help=f"Seconds to sleep between batches (default {DEFAULT_BATCH_PAUSE_SEC})",
    )
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    src = Path(args.src).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = _load_cfg(Path(args.config).expanduser() if args.config else None)

    proxy = (args.proxy or cfg.get("cpa_proxy") or cfg.get("proxy") or "").strip() or None
    remote = (not args.no_remote) and _config_bool(cfg.get("cpa_remote_inject"), default=False)
    cfg["cpa_remote_inject"] = remote
    cfg["cpa_remote_inject_require_chat_ok"] = True
    cfg["cpa_probe_chat"] = True
    cfg["cpa_probe_chat_required"] = True

    archive_dir = Path(args.archive_dir).expanduser() if args.archive_dir else (
        _ROOT / DEFAULT_ARCHIVE_DIR
    )
    archive_dir = archive_dir.resolve()
    ledger_path = out_dir / ARCHIVE_LEDGER_NAME
    skip_injected = not (args.force_reinject or args.no_skip_injected)
    batch_size = max(0, int(args.batch_size or 0))
    batch_pause = max(0.0, float(args.batch_pause or 0.0))

    files = sorted(src.glob("xai-*.json"))
    if args.limit and args.limit > 0:
        files = files[: args.limit]
    if not files:
        print(f"no xai-*.json under {src}", flush=True)
        return 2

    log_path = _ROOT / "logs" / f"import_auth_dir_{src.name}.jsonl"
    state_path = _ROOT / "logs" / f"import_auth_dir_{src.name}_state.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        print(msg, flush=True)

    print(
        f"import src={src} n={len(files)} out={out_dir} remote={remote} "
        f"force_refresh={bool(args.force_refresh)} skip_injected={skip_injected} "
        f"batch_size={batch_size or 'off'} batch_pause={batch_pause}s "
        f"archive={archive_dir} proxy={proxy or '(none)'}",
        flush=True,
    )
    results: list[dict] = []
    t0 = time.time()
    attempted_in_batch = 0
    batch_idx = 0
    for i, p in enumerate(files, 1):
        # Batch pause only counts non-skip attempts (real Clash/network work).
        if batch_size > 0 and attempted_in_batch >= batch_size:
            batch_idx += 1
            log(
                f"[import] batch {batch_idx} done (size={batch_size}); "
                f"pause {batch_pause}s to ease Clash load"
            )
            if batch_pause > 0:
                time.sleep(batch_pause)
            attempted_in_batch = 0

        rec = import_one(
            p,
            out_dir=out_dir,
            cfg=cfg,
            proxy=proxy,
            remote=remote,
            log=log,
            force_refresh=bool(args.force_refresh),
            force_reinject=bool(args.force_reinject),
            skip_injected=skip_injected,
            archive_dir=archive_dir,
            ledger_path=ledger_path,
        )
        # Archive every real attempt (ok or fail) so the account is "used".
        rec = _maybe_archive_attempt(
            rec, p, archive_dir=archive_dir, ledger_path=ledger_path
        )
        if not rec.get("skipped"):
            attempted_in_batch += 1
        results.append(rec)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(
            f"[{i}/{len(files)}] {rec.get('email')}: ok={rec.get('ok')} "
            f"stage={rec.get('stage')} src={rec.get('token_source')} "
            f"live={rec.get('remote_live_ok')} rot={rec.get('refresh_rotated')} "
            f"arch={rec.get('archived')} err={str(rec.get('error') or '')[:100]}",
            flush=True,
        )

    ok_n = sum(1 for r in results if r.get("ok"))
    state = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "src": str(src),
        "total": len(results),
        "ok": ok_n,
        "fail": len(results) - ok_n,
        "live_ok": sum(1 for r in results if r.get("remote_live_ok")),
        "inventory_ok": sum(1 for r in results if r.get("remote_inventory_ok")),
        "chat_ok": sum(1 for r in results if r.get("chat_ok")),
        "skipped_injected": sum(1 for r in results if r.get("stage") == "skip_injected"),
        "skipped_tested": sum(1 for r in results if r.get("stage") == "skip_tested"),
        "archived": sum(1 for r in results if r.get("archived")),
        "tested": sum(1 for r in results if r.get("tested")),
        "batch_size": batch_size,
        "batch_pause_s": batch_pause,
        "access_reuse": sum(1 for r in results if r.get("token_source") == "access_reuse"),
        "refreshed": sum(
            1
            for r in results
            if str(r.get("token_source") or "").startswith("refresh")
            or "retry_refresh" in str(r.get("token_source") or "")
        ),
        "refresh_fail": sum(
            1 for r in results if r.get("stage") in {"refresh", "tokens"} and not r.get("ok")
        ),
        "elapsed_s": round(time.time() - t0, 1),
        "log": str(log_path),
        "archive_dir": str(archive_dir),
        "ledger": str(ledger_path),
        "failed": [
            {
                "email": r.get("email"),
                "stage": r.get("stage"),
                "error": r.get("error"),
                "token_source": r.get("token_source"),
            }
            for r in results
            if not r.get("ok")
        ],
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("=== STATE ===", flush=True)
    print(json.dumps(state, ensure_ascii=False, indent=2), flush=True)
    # Product exit: skip_* counts as ok; only hard fails pull exit=1.
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
