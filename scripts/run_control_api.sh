#!/usr/bin/env bash
# Start project-owned Web control plane (FastAPI + static UI).
#
# Production (pxed): prefer durable secret files under $ROOT (mode 0600):
#   .control_api_session_secret   password-login cookie HMAC
#   .control_api_token            optional Bearer for scripts
#   .control_api_users.json       operators
# Bare `python -m apps.control_api` also auto-loads those files now; still prefer
# this wrapper so env + ops logs stay consistent.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export REGISTER_PROJECT_ROOT="${REGISTER_PROJECT_ROOT:-$ROOT}"
export CONTROL_API_HOST="${CONTROL_API_HOST:-127.0.0.1}"
export CONTROL_API_PORT="${CONTROL_API_PORT:-8787}"

_read_secret_file() {
  # first non-empty, non-# line; never echo value
  local f="$1"
  [[ -f "$f" ]] || return 1
  # shellcheck disable=SC2162
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line//$'\r'/}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    printf '%s' "$line"
    return 0
  done <"$f"
  return 1
}

SECRET_SRC="missing"
TOKEN_SRC="missing"

# Bearer token: env → file
if [[ -z "${CONTROL_API_TOKEN:-}" ]]; then
  if tok="$(_read_secret_file "$ROOT/.control_api_token")"; then
    export CONTROL_API_TOKEN="$tok"
    TOKEN_SRC="file:.control_api_token"
  fi
else
  TOKEN_SRC="env"
fi

# Session secret: env → file → token → ephemeral (dev)
if [[ -z "${CONTROL_API_SESSION_SECRET:-}" ]]; then
  if sec="$(_read_secret_file "$ROOT/.control_api_session_secret")"; then
    export CONTROL_API_SESSION_SECRET="$sec"
    SECRET_SRC="file:.control_api_session_secret"
  elif [[ -n "${CONTROL_API_TOKEN:-}" ]]; then
    export CONTROL_API_SESSION_SECRET="$CONTROL_API_TOKEN"
    SECRET_SRC="token"
  fi
else
  SECRET_SRC="env"
fi

if [[ -z "${CONTROL_API_SESSION_SECRET:-}" ]]; then
  # Ephemeral secret so password login works after first user is created (dev).
  export CONTROL_API_ALLOW_EPHEMERAL_SESSION="${CONTROL_API_ALLOW_EPHEMERAL_SESSION:-1}"
  SECRET_SRC="ephemeral(allow)"
fi

echo "[control_api] start root=$ROOT host=${CONTROL_API_HOST} port=${CONTROL_API_PORT}" >&2
echo "[control_api] auth session_secret=${SECRET_SRC} token=${TOKEN_SRC:-missing} users_file=$([ -f "$ROOT/.control_api_users.json" ] && echo yes || echo no)" >&2

if [[ "$SECRET_SRC" == "missing" || "$SECRET_SRC" == "ephemeral(allow)" ]]; then
  echo "[control_api] WARN password login needs a durable secret on production:" >&2
  echo "  printf '%s' \"\$(openssl rand -hex 32)\" > $ROOT/.control_api_session_secret && chmod 600 $ROOT/.control_api_session_secret" >&2
fi
if [[ ! -f "$ROOT/.control_api_users.json" && -z "${CONTROL_API_BOOTSTRAP_USER:-}" && -z "${CONTROL_API_TOKEN:-}" ]]; then
  echo "[control_api] No operators yet. Create one:" >&2
  echo "  uv run python scripts/control_api_user.py set admin" >&2
  echo "  # or once: CONTROL_API_BOOTSTRAP_USER=admin CONTROL_API_BOOTSTRAP_PASSWORD='…'" >&2
fi
if [[ -z "${CONTROL_API_TOKEN:-}" ]]; then
  echo "[control_api] NOTE: CONTROL_API_TOKEN unset — scripts without cookie need password login or open mode (no users)." >&2
fi
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  exec "$ROOT/.venv/bin/python" -m apps.control_api
fi
exec uv run python -m apps.control_api
