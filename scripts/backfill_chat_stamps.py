#!/usr/bin/env python3
"""Inventory / backfill chat_ok stamps on historical CPA xai-*.json files.

Ops tool for auth dirs minted before chat probe stamps existed (~mostly
unstamped pools). Default is inventory-only (no network).

With ``--probe``: re-probe chat (and models if needed) using the file's
access_token, then stamp chat_ok / entitlement_denied / chat_retryable /
import_gate via ``stamp_auth_chat_fields``. Entitlement denials also append
the ledger. Does NOT remint OAuth and does NOT remote-inject (use remint for
that after stamps exist).

Mid-tier probe (tebi CPA):
  - ``cpa_probe_via`` / ``--probe-via``: direct | cpa | hybrid (default hybrid)
  - unpinned ``cpa`` falls back to hybrid (direct gate + optional CPA smoke)
  - observational ``probe_via_cpa_ok`` never replaces ``chat_ok`` for inject

Examples (from project root):
  .venv/bin/python -u scripts/backfill_chat_stamps.py --inventory-only
  .venv/bin/python -u scripts/backfill_chat_stamps.py --probe --only-missing --limit 20
  .venv/bin/python -u scripts/backfill_chat_stamps.py --probe --email a@b.com --no-remote
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cpa_xai.probe import (  # noqa: E402
    apply_chat_probe_to_result,
    build_probe_transport,
    probe_chat_with_retries,
    probe_models,
    resolve_gate_probe_policy,
)
from cpa_xai.schema import DEFAULT_BASE_URL, credential_file_name  # noqa: E402
from cpa_xai.writer import (  # noqa: E402
    inventory_chat_stamps,
    is_entitlement_denied_auth,
    record_entitlement_denied,
    stamp_auth_chat_fields,
)


def _load_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"warn: config read failed: {e}", flush=True)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        k: v
        for k, v in raw.items()
        if not (isinstance(k, str) and (k.startswith("//") or k.startswith("#")))
    }


def _iter_auth_files(
    auth_dir: Path,
    *,
    only_email: str,
    only_missing: bool,
    include_denied: bool,
    limit: int,
) -> list[Path]:
    want = (only_email or "").strip().lower()
    out: list[Path] = []
    for p in sorted(auth_dir.glob("xai-*.json")):
        if "_selftest" in p.name:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        em = str(d.get("email") or "").strip().lower()
        if want and em != want:
            continue
        if only_missing and "chat_ok" in d:
            continue
        if not include_denied and is_entitlement_denied_auth(d):
            continue
        out.append(p)
        if limit and len(out) >= limit:
            break
    return out


def _resolve_probe_pin(
    *,
    email: str,
    path: Path,
    pin_mode: str,
    explicit_pin: str = "",
) -> str:
    if (explicit_pin or "").strip():
        return explicit_pin.strip()
    mode = (pin_mode or "auth_filename").strip().lower()
    if mode == "email":
        return (email or "").strip()
    if mode == "auth_filename":
        if email:
            return credential_file_name(email=email)
        return path.name
    return ""


def _probe_and_stamp(
    path: Path,
    *,
    base_url: str,
    proxy: str | None,
    log,
    probe_via: str = "hybrid",
    cpa_probe_base_url: str = "",
    cpa_probe_api_key: str = "",
    pin_mode: str = "auth_filename",
    pin_header: str = "X-CPA-Credential",
    allow_unpinned_cpa_gate: bool = False,
    probe_credential_pin: str = "",
) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": str(path), "error": f"read: {e}"}
    if not isinstance(d, dict):
        return {"ok": False, "path": str(path), "error": "not a dict"}
    email = str(d.get("email") or "").strip()
    token = str(d.get("access_token") or "").strip()
    if not token:
        return {"ok": False, "email": email, "path": str(path), "error": "missing access_token"}

    pin = _resolve_probe_pin(
        email=email,
        path=path,
        pin_mode=pin_mode,
        explicit_pin=probe_credential_pin,
    )
    policy = resolve_gate_probe_policy(
        via=probe_via,
        cpa_base_url=cpa_probe_base_url,
        cpa_api_key=cpa_probe_api_key,
        credential_pin=pin if str(probe_via or "").strip().lower() != "direct" else "",
        allow_unpinned_cpa_gate=bool(allow_unpinned_cpa_gate),
    )
    gate_via = str(policy.get("gate_via") or "direct")
    gate_transport = build_probe_transport(
        via=gate_via,
        upstream_base_url=base_url,
        cpa_base_url=cpa_probe_base_url,
        cpa_api_key=cpa_probe_api_key,
        access_token=token,
        credential_pin=pin if gate_via == "cpa" else "",
        pin_header=pin_header or "X-CPA-Credential",
    )
    log(
        f"probe policy via={probe_via!r} gate={gate_via} "
        f"cpa_smoke={policy.get('cpa_smoke')} reason={policy.get('reason')}"
    )

    pr = probe_models(token, base_url=base_url, proxy=proxy, transport=gate_transport)
    result: dict = {
        "email": email,
        "path": str(path),
        "probe_models": pr,
        "probe_gate_via": gate_via,
        "probe_policy_reason": policy.get("reason") or "",
        "probe_via_cpa_ok": None,
    }
    log(
        f"models ok={pr.get('ok')} status={pr.get('status')} "
        f"has_grok_45={pr.get('has_grok_45')} mode={pr.get('transport_mode')!r} "
        f"err={str(pr.get('error') or '')[:120]}"
    )

    if not pr.get("has_grok_45"):
        apply_chat_probe_to_result(
            result,
            None,
            models_missing=True,
            models_status=int(pr.get("status") or 0),
        )
    else:
        ch = probe_chat_with_retries(
            token,
            base_url=base_url,
            proxy=proxy,
            max_attempts=3,
            log=log,
            transport=gate_transport,
        )
        apply_chat_probe_to_result(result, ch)
        if result.get("entitlement_denied"):
            log("FAIL-FAST: chat entitlement_denied — ledger + stamp only (no remint)")

    # Observational CPA smoke only after gate chat_ok (never inject latch).
    if (
        policy.get("cpa_smoke")
        and result.get("chat_ok") is True
        and not result.get("entitlement_denied")
        and (cpa_probe_base_url or "").strip()
        and (cpa_probe_api_key or "").strip()
    ):
        cpa_t = build_probe_transport(
            via="cpa",
            upstream_base_url=base_url,
            cpa_base_url=cpa_probe_base_url,
            cpa_api_key=cpa_probe_api_key,
            access_token=token,
            credential_pin=pin,
            pin_header=pin_header or "X-CPA-Credential",
        )
        try:
            smoke = probe_chat_with_retries(
                token,
                base_url=base_url,
                proxy=proxy,
                max_attempts=1,
                log=log,
                transport=cpa_t,
            )
            result["probe_cpa_smoke"] = smoke
            result["probe_via_cpa_ok"] = bool(smoke.get("ok"))
            log(
                f"cpa smoke ok={smoke.get('ok')} status={smoke.get('status')} "
                f"mode={smoke.get('transport_mode')!r}"
            )
        except Exception as e:  # noqa: BLE001
            result["probe_via_cpa_ok"] = False
            result["probe_cpa_smoke_error"] = str(e)
            log(f"cpa smoke failed: {e}")

    try:
        updates = {
            "probe_gate_via": result.get("probe_gate_via"),
            "probe_policy_reason": result.get("probe_policy_reason"),
        }
        if result.get("probe_via_cpa_ok") is True or result.get("probe_via_cpa_ok") is False:
            updates["probe_via_cpa_ok"] = bool(result.get("probe_via_cpa_ok"))
        stamped = stamp_auth_chat_fields(path, result, updates=updates)
        result["import_gate"] = stamped.get("import_gate")
        if stamped.get("chat_ok") is None and "chat_ok" not in stamped:
            # incomplete stamp should not happen after full probe; surface for ops
            log(f"stamp incomplete keys={sorted(stamped.keys())}")
    except Exception as e:  # noqa: BLE001
        result["stamp_error"] = str(e)
        log(f"stamp failed: {e}")

    if result.get("entitlement_denied"):
        try:
            record_entitlement_denied(
                path.parent,
                email,
                extra={
                    "path": str(path),
                    "source": "backfill_chat_stamps",
                    "chat_error_code": result.get("chat_error_code"),
                },
            )
        except Exception as e:  # noqa: BLE001
            log(f"ledger write failed: {e}")

    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=str(_ROOT / "config.json"))
    ap.add_argument("--auth-dir", default="", help="Default: config cpa_auth_dir or ./cpa_auths")
    ap.add_argument("--inventory-only", action="store_true", help="Only print inventory stats")
    ap.add_argument("--probe", action="store_true", help="Re-probe chat and stamp files")
    ap.add_argument("--only-missing", action="store_true", help="Only files without chat_ok key")
    ap.add_argument("--include-denied", action="store_true", help="Also process stamped denied")
    ap.add_argument("--email", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=1.5)
    ap.add_argument(
        "--base-url",
        default="",
        help=f"Override probe base (default config/DEFAULT {DEFAULT_BASE_URL})",
    )
    ap.add_argument(
        "--probe-via",
        default="",
        help="Override cpa_probe_via: direct|cpa|hybrid (default hybrid)",
    )
    ap.add_argument(
        "--state",
        default=str(_ROOT / "logs" / "backfill_chat_stamps_state.json"),
    )
    args = ap.parse_args()

    cfg = _load_config(Path(args.config))
    auth_dir = Path(args.auth_dir or cfg.get("cpa_auth_dir") or (_ROOT / "cpa_auths"))
    if not auth_dir.is_absolute():
        auth_dir = (_ROOT / auth_dir).resolve()
    if not auth_dir.is_dir():
        print(f"auth_dir missing: {auth_dir}", flush=True)
        return 2

    inv = inventory_chat_stamps(auth_dir)
    print(f"auth_dir={auth_dir}", flush=True)
    print(json.dumps({"inventory": inv}, ensure_ascii=False, indent=2), flush=True)

    if args.inventory_only or not args.probe:
        if not args.probe and not args.inventory_only:
            print(
                "hint: pass --probe to re-probe+stamp, or --inventory-only to silence this hint",
                flush=True,
            )
        return 0

    base_url = (args.base_url or cfg.get("cpa_base_url") or DEFAULT_BASE_URL).rstrip("/")
    proxy = (cfg.get("proxy") or cfg.get("https_proxy") or "").strip() or None
    probe_via = (
        str(args.probe_via or cfg.get("cpa_probe_via") or "hybrid").strip().lower() or "hybrid"
    )
    if probe_via not in {"direct", "cpa", "hybrid"}:
        probe_via = "hybrid"
    cpa_probe_base_url = str(
        cfg.get("cpa_probe_base_url") or os.environ.get("CPA_PROBE_BASE_URL") or ""
    ).strip()
    cpa_probe_api_key = str(
        cfg.get("cpa_probe_api_key") or os.environ.get("CPA_PROBE_API_KEY") or ""
    ).strip()
    pin_mode = str(
        cfg.get("cpa_probe_credential_pin_mode") or "auth_filename"
    ).strip().lower() or "auth_filename"
    pin_header = str(
        cfg.get("cpa_probe_pin_header") or "X-CPA-Credential"
    ).strip() or "X-CPA-Credential"
    allow_unpinned = bool(cfg.get("cpa_probe_allow_unpinned_cpa_gate") is True)

    files = _iter_auth_files(
        auth_dir,
        only_email=args.email,
        only_missing=bool(args.only_missing),
        include_denied=bool(args.include_denied),
        limit=args.limit,
    )
    print(
        f"probe candidates={len(files)} only_missing={args.only_missing} "
        f"base_url={base_url} probe_via={probe_via} sleep={args.sleep}",
        flush=True,
    )
    if not files:
        print("nothing to probe", flush=True)
        return 0

    ok_n = denied_n = fail_n = 0
    t0 = time.time()
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    for i, p in enumerate(files, 1):
        email_hint = p.name

        def log(msg: str, _i=i, _p=email_hint) -> None:
            print(f"[{time.strftime('%H:%M:%S')}] [{_i}/{len(files)}] [{_p}] {msg}", flush=True)

        log("start")
        r = _probe_and_stamp(
            p,
            base_url=base_url,
            proxy=proxy,
            log=log,
            probe_via=probe_via,
            cpa_probe_base_url=cpa_probe_base_url,
            cpa_probe_api_key=cpa_probe_api_key,
            pin_mode=pin_mode,
            pin_header=pin_header,
            allow_unpinned_cpa_gate=allow_unpinned,
        )
        if r.get("entitlement_denied"):
            denied_n += 1
            log(f"DENIED gate={r.get('import_gate')}")
        elif r.get("chat_ok") is True:
            ok_n += 1
            log("chat_ok")
        else:
            fail_n += 1
            log(f"fail reason={r.get('fail_reason') or r.get('error')}")

        if i == 1 or i % 10 == 0 or i == len(files):
            state = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "done": i,
                "total": len(files),
                "chat_ok": ok_n,
                "chat_denied": denied_n,
                "chat_fail": fail_n,
                "elapsed_s": round(time.time() - t0, 1),
                "last_path": str(p),
                "last_chat_ok": r.get("chat_ok"),
                "last_import_gate": r.get("import_gate"),
                "probe_via": probe_via,
            }
            state_path.write_text(
                json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )

        if args.sleep and i < len(files):
            time.sleep(args.sleep)

    inv_after = inventory_chat_stamps(auth_dir)
    print(
        f"\n=== done chat_ok={ok_n} chat_denied={denied_n} chat_fail={fail_n} "
        f"elapsed={round(time.time()-t0,1)}s ===",
        flush=True,
    )
    print(json.dumps({"inventory_after": inv_after}, ensure_ascii=False, indent=2), flush=True)
    return 0 if denied_n + fail_n == 0 or ok_n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
