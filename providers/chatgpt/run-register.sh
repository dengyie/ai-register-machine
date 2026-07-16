#!/usr/bin/env bash
# ChatGPT / OpenAI platform register runner (in-process via register_core).
# Usage:
#   ./providers/chatgpt/run-register.sh [count]
#
# Egress switch (pick one):
#   REGISTER_EGRESS=core    # project mihomo .nodes :17897 (no Clash Verge)
#   REGISTER_EGRESS=clash   # external Clash/mihomo :7897
#   REGISTER_EGRESS=list    # only nodes.json / PROXY_LIST
#   REGISTER_EGRESS=direct  # no proxy
#   REGISTER_EGRESS=auto    # list → core → clash (default)
#
# Persist: python -m register_core nodes egress set core|clash|list|direct|auto
set -euo pipefail

COUNT="${COUNT:-${1:-1}}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

export REGISTER_EGRESS="${REGISTER_EGRESS:-${EGRESS_BACKEND:-${CHATGPT_EGRESS:-}}}"
export CHATGPT_PROXY="${CHATGPT_PROXY:-${MIMO_PROXY:-}}"
# Default: project Cloudflare Worker temp-mail (config.json cloudflare_api_base).
# Override with gmail_imap / tinyhost / duckmail when needed.
export CHATGPT_EMAIL_SOURCE="${CHATGPT_EMAIL_SOURCE:-cloudflare}"
export CHATGPT_PROXY_LIST="${CHATGPT_PROXY_LIST:-${PROXY_LIST:-}}"
export CHATGPT_PROXY_ROTATE_MODE="${CHATGPT_PROXY_ROTATE_MODE:-${PROXY_ROTATE_MODE:-}}"
export CHATGPT_PROXY_ROTATE_EVERY="${CHATGPT_PROXY_ROTATE_EVERY:-${PROXY_ROTATE_EVERY:-1}}"
export REGISTER_NODES_FILE="${REGISTER_NODES_FILE:-${NODES_FILE:-$ROOT/nodes.json}}"
export CLASH_PROXY="${CLASH_PROXY:-http://127.0.0.1:7897}"

SINK="${CHATGPT_SINK:-$ROOT/providers/chatgpt/output/pipeline.jsonl}"
mkdir -p "$(dirname "$SINK")"

echo "[chatgpt] COUNT=$COUNT egress=${REGISTER_EGRESS:-auto} proxy=${CHATGPT_PROXY:-'(from egress)'} proxy_list=${CHATGPT_PROXY_LIST:-'(nodes/core)'} rotate=${CHATGPT_PROXY_ROTATE_MODE:-auto} email_source=$CHATGPT_EMAIL_SOURCE" >&2

ARGS=(
  -m register_core run
  -p chatgpt
  -n "$COUNT"
  --email-source "${CHATGPT_EMAIL_SOURCE}"
  --sink "$SINK"
  --timeout "${CHATGPT_TIMEOUT:-900}"
)
if [[ -n "${REGISTER_EGRESS}" ]]; then
  ARGS+=(--egress "${REGISTER_EGRESS}")
fi
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
