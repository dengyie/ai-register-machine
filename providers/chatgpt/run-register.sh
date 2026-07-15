#!/usr/bin/env bash
# ChatGPT / OpenAI platform register runner (in-process via register_core).
# Usage:
#   ./providers/chatgpt/run-register.sh [count]
#   COUNT=1 CHATGPT_PROXY=http://127.0.0.1:7897 ./providers/chatgpt/run-register.sh
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
export CHATGPT_EMAIL_SOURCE="${CHATGPT_EMAIL_SOURCE:-tinyhost}"

SINK="${CHATGPT_SINK:-$ROOT/providers/chatgpt/output/pipeline.jsonl}"
mkdir -p "$(dirname "$SINK")"

echo "[chatgpt] COUNT=$COUNT proxy=$CHATGPT_PROXY email_source=$CHATGPT_EMAIL_SOURCE" >&2

exec "$PY" -m register_core run \
  -p chatgpt \
  -n "$COUNT" \
  --email-source "${CHATGPT_EMAIL_SOURCE}" \
  --sink "$SINK" \
  --timeout "${CHATGPT_TIMEOUT:-900}"
