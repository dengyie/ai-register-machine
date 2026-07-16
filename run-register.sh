#!/usr/bin/env bash
# Usage: bash run-register.sh [count] [threads]
# Env: GROK_NODE, GROK_CONFIG, HEADLESS_FLAG=--headless|--no-headless
# Exit code: register_cli.py product exit (0 product-ok free Build; 1 product-fail; 2 fatal).
# Do not mask with `| tee` alone — use PIPESTATUS[0] for the python/xvfb side.
set -u
cd "$(dirname "$0")"
ROOT="$(pwd)"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy
export NO_PROXY=127.0.0.1,localhost
export no_proxy=127.0.0.1,localhost

# start clash (mac-merged default on pxed)
export GROK_NODE="${GROK_NODE:-GVPS-AnyTLS-googlevps}"
if [[ -x "$ROOT/start-clash-for-grok.sh" ]]; then
  bash "$ROOT/start-clash-for-grok.sh" || exit 1
fi

# load .env line-safe when present
if [[ -f "$ROOT/.env" ]]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
    esac
    key=${line%%=*}
    val=${line#*=}
    export "$key=$val"
  done < "$ROOT/.env"
fi

export EMAIL_PROVIDER="${EMAIL_PROVIDER:-gmail}"
export PROXY="${PROXY:-http://127.0.0.1:7897}"
export CPA_PROXY="${CPA_PROXY:-http://127.0.0.1:7897}"
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/personal/browsers/ms-playwright}"
export DISPLAY="${DISPLAY:-}"

if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "$ROOT/.venv/bin/activate"
fi
COUNT=${1:-1}
THREADS=${2:-1}
HEADLESS_FLAG=${HEADLESS_FLAG:---no-headless}
TS=$(date +%Y%m%d_%H%M%S)
LOG="$ROOT/logs/run-${TS}.log"
mkdir -p "$ROOT/logs" "$ROOT/screenshots"

echo "=== register start count=$COUNT threads=$THREADS node=$GROK_NODE headless_flag=$HEADLESS_FLAG ===" | tee -a "$LOG"

set +e
if [[ "$HEADLESS_FLAG" == "--headless" ]]; then
  python -u register_cli.py --extra "$COUNT" --threads "$THREADS" --headless --fast --account-slot-retry 0 2>&1 | tee -a "$LOG"
  code=${PIPESTATUS[0]}
else
  xvfb-run -a -s "-screen 0 1280x900x24 -ac +extension GLX +render -noreset" \
    python -u register_cli.py --extra "$COUNT" --threads "$THREADS" --no-headless --fast --account-slot-retry 0 2>&1 | tee -a "$LOG"
  code=${PIPESTATUS[0]}
fi
set -e

echo "=== register_cli exit=$code (product: 0=ok 1=not product-usable 2=fatal) ===" | tee -a "$LOG"
exit "$code"
