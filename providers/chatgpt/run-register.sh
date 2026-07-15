#!/usr/bin/env bash
# ChatGPT / OpenAI platform register runner (in-process via register_core).
# Usage:
#   ./providers/chatgpt/run-register.sh [count]
#   COUNT=1 CHATGPT_PROXY=http://127.0.0.1:7897 ./providers/chatgpt/run-register.sh
# Self-controlled nodes (preferred — no Clash selector):
#   CHATGPT_PROXY_LIST='http://u:p@1.2.3.4:8080,http://u:p@5.6.7.8:8080' \
#     ./providers/chatgpt/run-register.sh 3
set -euo pipefail

COUNT="${COUNT:-${1:-1}}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

export CHATGPT_PROXY="${CHATGPT_PROXY:-${MIMO_PROXY:-http://127.0.0.1:7897}}"
export CHATGPT_EMAIL_SOURCE="${CHATGPT_EMAIL_SOURCE:-gmail_imap}"
# Self-controlled egress: set CHATGPT_PROXY_LIST / PROXY_LIST to a pool of
# upstream URLs. When set, rotation auto-selects list mode — no Clash node pick.
export CHATGPT_PROXY_LIST="${CHATGPT_PROXY_LIST:-${PROXY_LIST:-}}"
export CHATGPT_PROXY_ROTATE_MODE="${CHATGPT_PROXY_ROTATE_MODE:-${PROXY_ROTATE_MODE:-}}"
export CHATGPT_PROXY_ROTATE_EVERY="${CHATGPT_PROXY_ROTATE_EVERY:-${PROXY_ROTATE_EVERY:-1}}"

SINK="${CHATGPT_SINK:-$ROOT/providers/chatgpt/output/pipeline.jsonl}"
mkdir -p "$(dirname "$SINK")"

echo "[chatgpt] COUNT=$COUNT proxy=$CHATGPT_PROXY proxy_list=${CHATGPT_PROXY_LIST:-'(none)'} rotate=${CHATGPT_PROXY_ROTATE_MODE:-auto} email_source=$CHATGPT_EMAIL_SOURCE" >&2

ARGS=(
  -m register_core run
  -p chatgpt
  -n "$COUNT"
  --email-source "${CHATGPT_EMAIL_SOURCE}"
  --sink "$SINK"
  --timeout "${CHATGPT_TIMEOUT:-900}"
)
if [[ -n "${CHATGPT_PROXY}" ]]; then
  ARGS+=(--proxy "${CHATGPT_PROXY}")
fi
if [[ -n "${CHATGPT_PROXY_LIST}" ]]; then
  ARGS+=(--proxy-list "${CHATGPT_PROXY_LIST}")
fi
if [[ -n "${CHATGPT_PROXY_ROTATE_MODE}" ]]; then
  ARGS+=(--proxy-rotate "${CHATGPT_PROXY_ROTATE_MODE}")
fi
if [[ -n "${CHATGPT_PROXY_ROTATE_EVERY}" ]]; then
  ARGS+=(--proxy-rotate-every "${CHATGPT_PROXY_ROTATE_EVERY}")
fi

exec "$PY" "${ARGS[@]}"
