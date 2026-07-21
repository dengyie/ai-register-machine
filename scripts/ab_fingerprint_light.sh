#!/usr/bin/env bash
# Isolated A/B for browser_fingerprint_mode=off vs light (tier A).
#
# Does NOT touch production batch supervisor. Uses its own flock and only
# starts register_cli when /tmp/grok_register_cli.lock is free (no --no-cli-lock)
# so we never dual-Chromium with batch_dc1k_ns.
#
# Usage (on pxed under /data/grok-register):
#   bash scripts/ab_fingerprint_light.sh
#   AB_N=2 SMOKE_TIMEOUT=900 bash scripts/ab_fingerprint_light.sh
#   AB_ARMS="off light" bash scripts/ab_fingerprint_light.sh
#
# Env freeze (disk-first):
#   CPA_EXPORT_ENABLED=true CPA_PROBE_CHAT=false CPA_REMOTE_INJECT=false
# Default fingerprint remains off for production launchers; this script only
# passes --browser-fingerprint-mode per arm.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

if [[ -d /data/grok-register && -f /data/grok-register/register_cli.py ]]; then
  if [[ "$ROOT" != /data/grok-register && "$ROOT" != /personal/grok-register ]]; then
    # Prefer runtime when script was copied from monorepo checkout.
    if [[ -f /data/grok-register/scripts/ab_fingerprint_light.sh ]]; then
      :
    fi
  fi
fi

AB_LOCK=/tmp/grok_ab_fingerprint.lock
exec 8>"$AB_LOCK"
if ! flock -n 8; then
  echo "another ab_fingerprint holds $AB_LOCK; exit 1"
  exit 1
fi
echo $$ > "${AB_LOCK}.pid"

CLI_LOCK=/tmp/grok_register_cli.lock
SMOKE_TIMEOUT=${SMOKE_TIMEOUT:-900}
AB_N=${AB_N:-1}   # attempts per arm
AB_ARMS=${AB_ARMS:-"off light"}
AB_WAIT_CLI_SEC=${AB_WAIT_CLI_SEC:-120}  # wait for production CLI lock to free

TS=$(date +%Y%m%d_%H%M%S)
mkdir -p logs
SUMMARY="logs/ab_fingerprint_${TS}.summary.jsonl"
LOG="logs/ab_fingerprint_${TS}.log"
export AB_SUMMARY_PATH="$SUMMARY"
export AB_LOG_PATH="$LOG"

# Line-safe .env load
if [[ -f .env ]]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ""|\#*) continue ;; esac
    key=${line%%=*}
    val=${line#*=}
    case "$key" in
      EMAIL_PROVIDER|EMAIL_PROVIDERS|EMAIL_PROVIDER_STRATEGY|MAIL_TIMEOUT|PLAYWRIGHT_BROWSERS_PATH|PROXY|CPA_PROXY|GROK_NODE|DISPLAY)
        export "$key=$val"
        ;;
    esac
  done < .env
fi

if [[ -f .venv/bin/activate ]]; then
  # shellcheck source=/dev/null
  source .venv/bin/activate
fi

export EMAIL_PROVIDER=${SMOKE_EMAIL_PROVIDER:-cloudflare}
unset EMAIL_PROVIDERS || true
export MAIL_TIMEOUT=${MAIL_TIMEOUT:-20}
export CPA_EXPORT_ENABLED=true
export CPA_PROBE_CHAT=false
export CPA_REMOTE_INJECT=false
export CPA_PREFER_PROTOCOL=${CPA_PREFER_PROTOCOL:-false}
export PROXY=${PROXY:-http://127.0.0.1:7897}
export CPA_PROXY=${CPA_PROXY:-$PROXY}
export PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-/personal/browsers/ms-playwright}
export SKIP_CLASH_PREFLIGHT=${SKIP_CLASH_PREFLIGHT:-1}
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy || true
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost

PY=${ROOT}/.venv/bin/python
if [[ ! -x "$PY" ]]; then
  PY=python3
fi

baseline_complete() {
  "$PY" - <<'PY'
import json
from pathlib import Path
need = ["access_token", "refresh_token", "email", "base_url", "token_endpoint", "headers"]
xs = list(Path("cpa_auths").glob("xai-*.json")) if Path("cpa_auths").exists() else []
complete = 0
for f in xs:
    try:
        j = json.loads(f.read_text())
    except Exception:
        continue
    if all((j.get(k) or "") for k in need):
        complete += 1
print(complete)
PY
}

wait_cli_lock_free() {
  local waited=0
  while ! flock -n 9 2>/dev/null; do
    if (( waited >= AB_WAIT_CLI_SEC )); then
      echo "[ab] CLI lock still held after ${AB_WAIT_CLI_SEC}s; refuse dual Chromium; exit 2"
      return 2
    fi
    echo "[ab] waiting for $CLI_LOCK free (${waited}/${AB_WAIT_CLI_SEC}s)..."
    sleep 5
    waited=$((waited + 5))
  done
  # We only probed; release immediately so register_cli can take it.
  flock -u 9 2>/dev/null || true
  return 0
}

# Open CLI lock fd for probing (non-blocking try)
exec 9>"$CLI_LOCK"

{
  echo "=== ab_fingerprint start $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "root=$ROOT arms=$AB_ARMS n=$AB_N timeout=$SMOKE_TIMEOUT"
  echo "EMAIL_PROVIDER=$EMAIL_PROVIDER PROXY=$PROXY SKIP_CLASH_PREFLIGHT=$SKIP_CLASH_PREFLIGHT"
  echo "summary=$SUMMARY"

  overall_base=$(baseline_complete)
  echo "overall_baseline_complete=$overall_base"

  for arm in $AB_ARMS; do
    arm=$(echo "$arm" | tr -d '[:space:]')
    [[ -z "$arm" ]] && continue
    if [[ "$arm" != "off" && "$arm" != "light" ]]; then
      echo "[ab] skip unknown arm=$arm"
      continue
    fi
    for i in $(seq 1 "$AB_N"); do
      echo "--- arm=$arm attempt=$i/$(echo $AB_N) $(date -u +%Y-%m-%dT%H:%M:%SZ) ---"
      if ! wait_cli_lock_free; then
        echo "{\"arm\":\"$arm\",\"attempt\":$i,\"status\":\"cli_lock_busy\",\"product_ok\":0}" >> "$SUMMARY"
        echo "[ab] abort remaining arms due to CLI lock" | tee -a "$LOG"
        exit 2
      fi
      base=$(baseline_complete)
      echo "baseline_complete=$base arm=$arm"
      set +e
      if command -v xvfb-run >/dev/null 2>&1 && [[ -z "${DISPLAY:-}" || "${FORCE_XVFB:-0}" == "1" ]]; then
        timeout "$SMOKE_TIMEOUT" xvfb-run -a -s "-screen 0 1280x900x24 -ac +extension GLX +render -noreset -nolisten tcp" \
          "$PY" -u register_cli.py --extra 1 --threads 1 --no-headless --fast \
          --browser-fingerprint-mode "$arm"
        code=$?
      else
        timeout "$SMOKE_TIMEOUT" \
          "$PY" -u register_cli.py --extra 1 --threads 1 --no-headless --fast \
          --browser-fingerprint-mode "$arm"
        code=$?
      fi
      set -e
      now=$(baseline_complete)
      delta=$((now - base))
      product_ok=0
      if (( delta > 0 )); then product_ok=1; fi
      echo "arm=$arm attempt=$i exit=$code delta=$delta product_ok=$product_ok"
      echo "{\"arm\":\"$arm\",\"attempt\":$i,\"exit\":$code,\"delta\":$delta,\"product_ok\":$product_ok,\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" >> "$SUMMARY"
      # brief cool between arms to avoid thrashing mail/proxy
      sleep 3
    done
  done

  echo "=== ab_fingerprint summary ==="
  if [[ -f "$SUMMARY" ]]; then
    cat "$SUMMARY"
    "$PY" - <<'PY'
import json, os
from collections import defaultdict
path = os.environ.get("AB_SUMMARY_PATH") or ""
rows = []
if path and os.path.isfile(path):
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
by = defaultdict(lambda: {"n": 0, "ok": 0, "busy": 0})
for r in rows:
    a = r.get("arm") or "?"
    by[a]["n"] += 1
    by[a]["ok"] += int(r.get("product_ok") or 0)
    if r.get("status") == "cli_lock_busy":
        by[a]["busy"] += 1
print("AGG", {k: dict(v) for k, v in by.items()})
print("NOTE production default remains browser_fingerprint_mode=off until AGG favors light")
PY
  fi
  echo "=== ab_fingerprint end $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
} 2>&1 | tee -a "$LOG"

# exit 0 if at least one product_ok across arms; 1 otherwise; 2 lock busy already exited
if [[ -f "$SUMMARY" ]] && grep -q '"product_ok":1' "$SUMMARY"; then
  exit 0
fi
exit 1
