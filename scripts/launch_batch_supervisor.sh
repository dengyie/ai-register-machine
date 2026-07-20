#!/usr/bin/env bash
# Bulk Grok reg+mint disk supervisor on pxed (single-instance via flock).
# Restarts after fatal/exit until target new complete xai-*.json (access+refresh).
# Modes:
#   residential — Clash front tunnel + 1024proxy HTTP CONNECT (家宽)
#   ordinary    — Clash mixed-port only (普通节点轮换)
# Ordinary: healthy-only preflight before batch / after zero-gain;
# fail-fast Clash next-node on stuck; hybrid browser recycle (soft until recycle_every) via SUPERVISOR_CHUNK (default 3).
#
# CPA inject contract (disk-first):
#   - Per-account mint path ALWAYS forces CPA_REMOTE_INJECT=false (never SSH mid-batch).
#   - If inject intent is on (CPA_BATCH_END_INJECT / pre-freeze CPA_REMOTE_INJECT /
#     config.json cpa_remote_inject), AFTER target complete run ONE unified import:
#     scripts/import_cpa_auth_dir.py --batch-size 5 (healthy-only + archive).
set -u
cd /personal/grok-register || exit 1

MODE=${1:-residential}
TARGET=${2:-100}
THREADS=${3:-1}
TAG_PREFIX=${4:-batch}

LOCK=/tmp/grok_batch_supervisor.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "another supervisor holds $LOCK; exit"
  exit 1
fi
echo $$ > "${LOCK}.pid"

TS0=$(date +%Y%m%d_%H%M%S)
RUN_TAG=${TAG_PREFIX}_${MODE}_${TARGET}_${TS0}
SUP_LOG=logs/${RUN_TAG}.supervisor.log
STATE=logs/${RUN_TAG}.state.json
IMPORT_LOG=logs/${RUN_TAG}.cpa_import.log
mkdir -p logs

RES_CREDS=(
  "cchv57025-region-Rand-sid-itCWTF4X-t-5:tyfnvdhr"
  "cchv57025-region-Rand-sid-7X3n2Kn1-t-5:tyfnvdhr"
  "cchv57025-region-Rand-sid-tHzv1pL8-t-5:tyfnvdhr"
  "cchv57025-region-Rand-sid-mzTjvSfh-t-5:tyfnvdhr"
  "cchv57025-region-Rand-sid-bQ6kiCiJ-t-5:tyfnvdhr"
)
RES_HOST=us.1024proxy.io:3000

if [[ -f .env ]]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ""|\#*) continue ;; esac
    key=${line%%=*}
    val=${line#*=}
    export "$key=$val"
  done < .env
fi

# shellcheck source=/dev/null
source .venv/bin/activate

# Capture inject *intent* before freezing per-account inject off.
# Intent sources (any true wins): CPA_BATCH_END_INJECT, CPA_REMOTE_INJECT (env/.env),
# config.json cpa_remote_inject. Mint path still always uses false.
_truthy() {
  case "${1:-}" in 1|true|TRUE|yes|YES|on|ON) return 0 ;; *) return 1 ;; esac
}
BATCH_CPA_INJECT_INTENT=false
if _truthy "${CPA_BATCH_END_INJECT:-}"; then
  BATCH_CPA_INJECT_INTENT=true
elif _truthy "${CPA_REMOTE_INJECT:-}"; then
  BATCH_CPA_INJECT_INTENT=true
elif [[ -f config.json ]] && .venv/bin/python - <<'PY'
import json, sys
from pathlib import Path
try:
    c = json.loads(Path("config.json").read_text(encoding="utf-8"))
except Exception:
    sys.exit(1)
v = c.get("cpa_remote_inject")
sys.exit(0 if v is True or str(v).lower() in {"1", "true", "yes", "on"} else 1)
PY
then
  BATCH_CPA_INJECT_INTENT=true
fi

export EMAIL_PROVIDER=${EMAIL_PROVIDER:-cloudflare}
export CPA_EXPORT_ENABLED=true
export CPA_PROBE_CHAT=false
# Always freeze per-account remote inject during bulk mint (disk-first).
export CPA_REMOTE_INJECT=false
export PLAYWRIGHT_BROWSERS_PATH=${PLAYWRIGHT_BROWSERS_PATH:-/personal/browsers/ms-playwright}
# Ordinary mode preflights Clash leaves; residential sets SKIP=1 (1024proxy).
export SKIP_CLASH_PREFLIGHT=${SKIP_CLASH_PREFLIGHT:-0}
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy || true
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost

# Batch-end unified CPA import (healthy-only, batch-of-5). Never blocks mint.
run_batch_end_cpa_import() {
  local why="${1:-target_reached}"
  local batch_size="${CPA_BATCH_IMPORT_SIZE:-5}"
  local batch_pause="${CPA_BATCH_IMPORT_PAUSE:-3}"
  local proxy="${CPA_BATCH_IMPORT_PROXY:-http://127.0.0.1:7897}"
  local cfg="${CPA_BATCH_IMPORT_CONFIG:-}"
  local import_py=scripts/import_cpa_auth_dir.py

  if [[ ! -f "$import_py" ]]; then
    echo "[supervisor] batch-end import SKIP: missing $import_py why=$why" | tee -a "$SUP_LOG"
    return 1
  fi

  # Prefer explicit import config; else production config.json (import --remote forces inject).
  if [[ -z "$cfg" ]]; then
    if [[ -f output/import_all_product_config.json ]]; then
      cfg=output/import_all_product_config.json
    else
      cfg=config.json
    fi
  fi

  echo "[supervisor] batch-end CPA import START why=$why cfg=$cfg batch=$batch_size pause=${batch_pause}s proxy=$proxy log=$IMPORT_LOG" | tee -a "$SUP_LOG"
  set +e
  # --remote: force live/inventory inject even if production config keeps cpa_remote_inject=false
  # (mint path must stay inject-off; import is the only place that pushes tebi).
  .venv/bin/python -u "$import_py" \
    --src cpa_auths \
    --out-dir cpa_auths \
    --config "$cfg" \
    --proxy "$proxy" \
    --remote \
    --batch-size "$batch_size" \
    --batch-pause "$batch_pause" \
    >"$IMPORT_LOG" 2>&1
  local ic=$?
  set -e
  if grep -q "=== STATE ===" "$IMPORT_LOG" 2>/dev/null; then
    awk '/=== STATE ===/{p=1} p' "$IMPORT_LOG" | head -40 | tee -a "$SUP_LOG"
  else
    tail -20 "$IMPORT_LOG" | tee -a "$SUP_LOG"
  fi
  echo "[supervisor] batch-end CPA import DONE exit=$ic log=$IMPORT_LOG" | tee -a "$SUP_LOG"
  return "$ic"
}

baseline_count() {
  .venv/bin/python - <<'PY'
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
acc = sum(1 for l in Path("accounts_cli.txt").read_text().splitlines() if l.strip()) if Path("accounts_cli.txt").exists() else 0
print(f"{len(xs)} {complete} {acc}")
PY
}


run_clash_preflight() {
  # Probe Clash leaves, rewrite GROK groups to healthy-only, restart mihomo.
  local why="${1:-batch}"
  if [[ "${SKIP_CLASH_PREFLIGHT:-0}" == "1" ]]; then
    echo "[supervisor] preflight skipped SKIP_CLASH_PREFLIGHT=1 why=$why" | tee -a "$SUP_LOG"
    return 0
  fi
  if [[ ! -f "$ROOT/preflight-clash-nodes.sh" ]]; then
    echo "[supervisor] preflight script missing; skip why=$why" | tee -a "$SUP_LOG"
    return 0
  fi
  echo "[supervisor] preflight-clash-nodes why=$why ..." | tee -a "$SUP_LOG"
  set +e
  bash "$ROOT/preflight-clash-nodes.sh" >>"$SUP_LOG" 2>&1
  local pc=$?
  set -e
  if [[ $pc -ne 0 ]]; then
    echo "[supervisor] preflight exit=$pc why=$why (healthy=0?)" | tee -a "$SUP_LOG"
    if [[ "${PREFLIGHT_REQUIRED:-0}" == "1" ]]; then
      return "$pc"
    fi
    return 0
  fi
  echo "[supervisor] preflight OK why=$why" | tee -a "$SUP_LOG"
  return 0
}

force_clash_next_node() {
  # Lightweight path switch: advance dedicated GROK group to next leaf.
  set +e
  .venv/bin/python - <<'PYN'
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
try:
    from proxy_rotate import clash_list_nodes, clash_switch_node
except Exception as e:
    print(f"[supervisor] force_clash_next import fail: {e}")
    raise SystemExit(0)
cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
api = str(cfg.get("clash_api") or "http://127.0.0.1:9090")
secret = str(cfg.get("clash_secret") or "")
group = str(cfg.get("clash_proxy_group") or "GROK-REG")
excl = str(cfg.get("clash_node_exclude") or "")
incl = str(cfg.get("clash_node_include") or "")
try:
    nodes, now, _ = clash_list_nodes(
        api, group, secret=secret, exclude_re=excl, include_re=incl
    )
except Exception as e:
    print(f"[supervisor] force_clash_next list fail: {e}")
    raise SystemExit(0)
if not nodes:
    print("[supervisor] force_clash_next: empty pool")
    raise SystemExit(0)
try:
    idx = nodes.index(now)
    nxt = nodes[(idx + 1) % len(nodes)]
except ValueError:
    nxt = nodes[0]
if nxt == now:
    print(f"[supervisor] force_clash_next: single node {now}")
    raise SystemExit(0)
try:
    clash_switch_node(api, group, nxt, secret=secret, flush=True)
    print(f"[supervisor] force_clash_next: {now} -> {nxt} pool={len(nodes)}")
except Exception as e:
    print(f"[supervisor] force_clash_next switch fail: {e}")
PYN
  set -e
}

ROOT="$(pwd)"

read -r BASE_TOTAL BASE_COMPLETE BASE_ACC <<<"$(baseline_count)"
GOAL_COMPLETE=$((BASE_COMPLETE + TARGET))

{
  echo "=== supervisor $RUN_TAG ==="
  echo "start_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "pid=$$ mode=$MODE target_new=$TARGET threads=$THREADS"
  echo "baseline total=$BASE_TOTAL complete=$BASE_COMPLETE accounts=$BASE_ACC goal_complete=$GOAL_COMPLETE"
  echo "CPA_PROBE_CHAT=false per_account_inject=false export=true (mint writes access+refresh)"
  echo "batch_end_cpa_inject_intent=$BATCH_CPA_INJECT_INTENT (unified import after TARGET only)"
  echo "success criterion: new complete xai-*.json with refresh_token (not chat_ok)"
  echo "lock=flock $LOCK (no inter-sub pkill)"
} | tee "$SUP_LOG"

echo "{\"baseline_complete\":$BASE_COMPLETE,\"target_new\":$TARGET,\"goal_complete\":$GOAL_COMPLETE,\"mode\":\"$MODE\",\"pid\":$$,\"batch_end_cpa_inject\":$BATCH_CPA_INJECT_INTENT}" > "$STATE"

attempt=0
max_attempts=200
deadline=$(( $(date +%s) + 12*3600 ))
consecutive_zero=0
HIT_TARGET=0

while true; do
  now=$(date +%s)
  if (( now > deadline )); then
    echo "[supervisor] deadline reached" | tee -a "$SUP_LOG"
    break
  fi
  read -r CUR_TOTAL CUR_COMPLETE CUR_ACC <<<"$(baseline_count)"
  NEW=$((CUR_COMPLETE - BASE_COMPLETE))
  echo "[supervisor] $(date -u +%H:%M:%SZ) complete=$CUR_COMPLETE (+$NEW/$TARGET) accounts=$CUR_ACC attempt=$attempt" | tee -a "$SUP_LOG"
  if (( CUR_COMPLETE >= GOAL_COMPLETE )); then
    echo "[supervisor] TARGET REACHED +$NEW complete auths" | tee -a "$SUP_LOG"
    HIT_TARGET=1
    break
  fi
  if (( attempt >= max_attempts )); then
    echo "[supervisor] max_attempts=$max_attempts" | tee -a "$SUP_LOG"
    break
  fi
  attempt=$((attempt + 1))
  remain=$((GOAL_COMPLETE - CUR_COMPLETE))
  # chunk=1 kills in-process browser reuse. ordinary default 3 (SUPERVISOR_CHUNK).
  # Turnstile fatal still stops whole register_cli; smaller remain handled below.
  if [[ "$MODE" == "residential" ]]; then
    chunk=${SUPERVISOR_CHUNK:-2}
  else
    chunk=${SUPERVISOR_CHUNK:-3}
  fi
  if (( remain < chunk )); then chunk=$remain; fi
  if (( chunk < 1 )); then chunk=1; fi

  SUB_TS=$(date +%Y%m%d_%H%M%S)
  SUB_LOG=logs/${RUN_TAG}.sub${attempt}_${SUB_TS}.log

  if [[ "$MODE" == "residential" ]]; then
    idx=$(( (attempt - 1) % ${#RES_CREDS[@]} ))
    cred=${RES_CREDS[$idx]}
    user=${cred%%:*}
    pass=${cred#*:}
    export PROXY="http://${user}:${pass}@${RES_HOST}"
    export CPA_PROXY="$PROXY"
    export REGISTER_FRONT_PROXY=http://127.0.0.1:7897
    export FRONT_PROXY=http://127.0.0.1:7897
    export PROXY_ROTATE_MODE=off
    # Residential egress is 1024proxy CONNECT, not Clash leaf pool.
    export SKIP_CLASH_PREFLIGHT=1
    echo "[supervisor] sub=$attempt chunk=$chunk mode=residential front=7897 sid_idx=$idx" | tee -a "$SUP_LOG"
  else
    export PROXY=http://127.0.0.1:7897
    export CPA_PROXY=http://127.0.0.1:7897
    unset REGISTER_FRONT_PROXY FRONT_PROXY || true
    export PROXY_ROTATE_MODE=clash
    export SKIP_CLASH_PREFLIGHT=${SKIP_CLASH_PREFLIGHT_ORDINARY:-0}
    # Node detection: healthy-only before first ordinary sub, and after zero-gain.
    if (( attempt == 1 || consecutive_zero >= 1 )); then
      if (( consecutive_zero >= 2 )); then
        run_clash_preflight "ordinary_sub${attempt}_zero${consecutive_zero}"
      elif (( attempt == 1 )); then
        run_clash_preflight "ordinary_batch_start"
      else
        force_clash_next_node
      fi
    fi
    echo "[supervisor] sub=$attempt chunk=$chunk mode=ordinary clash_rotate zero=$consecutive_zero" | tee -a "$SUP_LOG"
  fi

  export CPA_EXPORT_ENABLED=true
  export CPA_PROBE_CHAT=false
  export CPA_REMOTE_INJECT=false
  # PKCE consent_action_missing is currently 100% fail — skip to browser residual.
  export CPA_PREFER_PROTOCOL=false

  before_complete=$CUR_COMPLETE

  # Do NOT pkill leftover register_cli here — that races multi-instance and kills live work.
  # We await each sub to completion; only one sub runs at a time under flock.
  set +e
  # slot-retry>=1: browser_boot/ERR_CONNECTION_CLOSED can force-rotate + retry
  SLOT_RETRY=${ACCOUNT_SLOT_RETRY:-2}
  xvfb-run -a -s "-screen 0 1280x900x24 -ac +extension GLX +render -noreset" \
    python -u register_cli.py --extra "$chunk" --threads "$THREADS" --no-headless --fast \
      --account-slot-retry "$SLOT_RETRY" \
      --browser-recycle-mode hybrid \
      --proxy-rotate clash --proxy-rotate-every 1 \
    >"$SUB_LOG" 2>&1
  code=$?
  set -e
  echo "[supervisor] sub=$attempt exit=$code log=$SUB_LOG" | tee -a "$SUP_LOG"
  if grep -qE "SUMMARY_JSON|=== 完成|Fatal|FAIL-FAST|wrote |注册成功" "$SUB_LOG" 2>/dev/null; then
    grep -E "SUMMARY_JSON|=== 完成|注册成功|Fatal|FAIL-FAST|wrote |token_ok" "$SUB_LOG" | tail -12 | tee -a "$SUP_LOG"
  else
    tail -12 "$SUB_LOG" | tee -a "$SUP_LOG"
  fi

  read -r _ AFTER_COMPLETE _ <<<"$(baseline_count)"
  gained=$((AFTER_COMPLETE - before_complete))
  if (( gained <= 0 )); then
    consecutive_zero=$((consecutive_zero + 1))
  else
    consecutive_zero=0
  fi
  echo "[supervisor] sub=$attempt gained_complete=$gained consecutive_zero=$consecutive_zero" | tee -a "$SUP_LOG"

  # Fail-fast path switch on zero-gain (ordinary): do not only sleep on dead nodes.
  if (( consecutive_zero >= 1 )) && [[ "$MODE" == "ordinary" ]]; then
    if (( consecutive_zero >= 3 )); then
      run_clash_preflight "post_sub_zero${consecutive_zero}"
    else
      force_clash_next_node
    fi
  fi
  if (( consecutive_zero >= 8 )); then
    sleep 20
  elif (( consecutive_zero >= 4 )); then
    sleep 8
  elif (( consecutive_zero >= 2 )); then
    sleep 4
  else
    sleep 2
  fi
done

read -r END_TOTAL END_COMPLETE END_ACC <<<"$(baseline_count)"
NEW=$((END_COMPLETE - BASE_COMPLETE))
{
  echo "=== supervisor done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  echo "mode=$MODE new_complete=$NEW target=$TARGET final_complete=$END_COMPLETE accounts=$END_ACC hit_target=$HIT_TARGET"
  echo "batch_end_cpa_inject_intent=$BATCH_CPA_INJECT_INTENT"
  echo "STATE=$STATE SUP_LOG=$SUP_LOG"
} | tee -a "$SUP_LOG"

.venv/bin/python - <<PY | tee -a "$SUP_LOG"
import json
from pathlib import Path
sup = Path("$SUP_LOG")
start = sup.stat().st_mtime if sup.exists() else 0
need = ["access_token", "refresh_token", "email", "base_url", "token_endpoint", "headers"]
new, bad = [], []
for f in Path("cpa_auths").glob("xai-*.json"):
    if f.stat().st_mtime < start - 2:
        continue
    try:
        j = json.loads(f.read_text())
    except Exception as e:
        bad.append((f.name, str(e))); continue
    miss = [k for k in need if not j.get(k)]
    if miss:
        bad.append((f.name, miss))
    else:
        new.append(f.name)
print(f"audit_new_complete={len(new)} bad={len(bad)}")
if bad[:5]:
    print("bad_sample", bad[:5])
if new[:5]:
    print("new_sample", new[:5])
PY

# Unified CPA inject only after whole batch goal; never mid-mint / never if target missed.
if [[ "$BATCH_CPA_INJECT_INTENT" == "true" ]]; then
  if (( HIT_TARGET == 1 )); then
    set +e
    run_batch_end_cpa_import "target_reached"
    IMPORT_EXIT=$?
    set -e
    # Soft: import failure must not rewrite disk-first success of the batch.
    echo "[supervisor] batch-end import exit=$IMPORT_EXIT (soft; mint product already on disk)" | tee -a "$SUP_LOG"
  else
    echo "[supervisor] batch-end CPA import SKIP: target not reached (hit_target=0 intent=true)" | tee -a "$SUP_LOG"
  fi
else
  echo "[supervisor] batch-end CPA import SKIP: intent=false (set CPA_BATCH_END_INJECT=true or config cpa_remote_inject)" | tee -a "$SUP_LOG"
fi
