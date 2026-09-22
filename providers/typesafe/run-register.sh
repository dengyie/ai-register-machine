#!/usr/bin/env bash
# typesafe.ai / jev console register runner (in-process via register_core).
# Usage:
#   ./providers/typesafe/run-register.sh [count]
set -euo pipefail

COUNT="${COUNT:-${1:-1}}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

export REGISTER_EGRESS="${REGISTER_EGRESS:-${EGRESS_BACKEND:-${TYPESAFE_EGRESS:-}}}"
export TYPESAFE_PROXY="${TYPESAFE_PROXY:-${CHATGPT_PROXY:-${MIMO_PROXY:-}}}"
# Magic-link needs a full mail body. tinyhost is the default; OTP-only
# wrappers (cloudflare/gmail) cannot extract the Stytch URL.
export TYPESAFE_EMAIL_SOURCE="${TYPESAFE_EMAIL_SOURCE:-tinyhost}"
export TYPESAFE_EMAIL_DOMAIN="${TYPESAFE_EMAIL_DOMAIN:-${EMAIL_DOMAIN:-}}"
export TYPESAFE_PROXY_LIST="${TYPESAFE_PROXY_LIST:-${PROXY_LIST:-}}"
export TYPESAFE_PROXY_ROTATE_MODE="${TYPESAFE_PROXY_ROTATE_MODE:-${PROXY_ROTATE_MODE:-}}"
export TYPESAFE_PROXY_ROTATE_EVERY="${TYPESAFE_PROXY_ROTATE_EVERY:-${PROXY_ROTATE_EVERY:-1}}"
export REGISTER_NODES_FILE="${REGISTER_NODES_FILE:-${NODES_FILE:-$ROOT/nodes.json}}"
export CLASH_PROXY="${CLASH_PROXY:-http://127.0.0.1:7897}"

SINK="${TYPESAFE_SINK:-$ROOT/providers/typesafe/output/pipeline.jsonl}"
mkdir -p "$(dirname "$SINK")"

echo "[typesafe] COUNT=$COUNT egress=${REGISTER_EGRESS:-auto} proxy=${TYPESAFE_PROXY:-'(from egress)'} email_source=$TYPESAFE_EMAIL_SOURCE" >&2

ARGS=(
  -m register_core run
  -p typesafe
  -n "$COUNT"
  --email-source "${TYPESAFE_EMAIL_SOURCE}"
  --sink "$SINK"
  --timeout "${TYPESAFE_TIMEOUT:-900}"
)
if [[ -n "${REGISTER_EGRESS}" ]]; then
  ARGS+=(--egress "${REGISTER_EGRESS}")
fi
if [[ -n "${TYPESAFE_PROXY}" ]]; then
  ARGS+=(--proxy "${TYPESAFE_PROXY}")
fi
if [[ -n "${TYPESAFE_PROXY_LIST}" ]]; then
  ARGS+=(--proxy-list "${TYPESAFE_PROXY_LIST}")
fi
if [[ -n "${TYPESAFE_PROXY_ROTATE_MODE}" ]]; then
  ARGS+=(--proxy-rotate "${TYPESAFE_PROXY_ROTATE_MODE}")
fi
if [[ -n "${TYPESAFE_PROXY_ROTATE_EVERY}" ]]; then
  ARGS+=(--proxy-rotate-every "${TYPESAFE_PROXY_ROTATE_EVERY}")
fi

exec "$PY" "${ARGS[@]}"
