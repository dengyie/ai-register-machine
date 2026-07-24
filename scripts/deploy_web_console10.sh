#!/usr/bin/env bash
# scripts/deploy_web_console10.sh — single source for manual + GitHub Actions deploy
# Does NOT stop batch/coinbot. Static SPA only.
#
# control_api mounts StaticFiles at apps/web (NOT apps/web/dist):
#   web_dir = .../apps/web  + index.html + assets/
# so we publish dist/* into that web root, and also keep apps/web/dist in sync.
#
# Env:
#   PXED_HOST              ssh target (default: pxed)
#   PXED_WEB               remote web root (default: /data/grok-register/apps/web)
#                          must stay under /data/grok-register (sanitized)
#   PXED_SSH_PORT          default 22
#   PXED_SSH_KEY           optional private key path (IdentitiesOnly)
#   PXED_SSH_PRIVATE_KEY   optional PEM contents (written to a temp key file)
#   PXED_KNOWN_HOSTS       optional known_hosts body (required for non-interactive CI)
#   PXED_KNOWN_HOSTS_FILE  optional path to known_hosts (alternative to body)
#   SKIP_BUILD=1           use existing apps/web/dist (or CONSOLE10_TGZ)
#   CONSOLE10_TGZ          prebuilt tarball of dist contents; skips local build
#   DRY_RUN=1              pack only; no scp/ssh
#   PRUNE_STALE_ASSETS=1   default 1 — remove remote assets not in the new package
set -euo pipefail
export COPYFILE_DISABLE=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${PXED_HOST:-pxed}"
REMOTE="${PXED_WEB:-/data/grok-register/apps/web}"
PORT="${PXED_SSH_PORT:-22}"
TGZ="${CONSOLE10_TGZ:-/tmp/console10-webroot.tgz}"
PRUNE_STALE_ASSETS="${PRUNE_STALE_ASSETS:-1}"
CLEANUPS=()

cleanup() {
  local p
  for p in "${CLEANUPS[@]:-}"; do
    rm -rf "$p" 2>/dev/null || true
  done
}
trap cleanup EXIT

# --- sanitize remote path (C2) ---
while [[ "$REMOTE" == */ && "$REMOTE" != / ]]; do
  REMOTE="${REMOTE%/}"
done
case "$REMOTE" in
  /data/grok-register|/data/grok-register/*) ;;
  *)
    echo "[deploy] PXED_WEB must be under /data/grok-register, got: $REMOTE" >&2
    exit 2
    ;;
esac
if [[ "$REMOTE" == *..* ]]; then
  echo "[deploy] PXED_WEB must not contain .." >&2
  exit 2
fi
if [[ ! "$REMOTE" =~ ^/data/grok-register(/[A-Za-z0-9._+-]+)*$ ]]; then
  echo "[deploy] PXED_WEB has unsafe characters: $REMOTE" >&2
  exit 2
fi

# --- SSH identity / known_hosts ---
KEY_FILE="${PXED_SSH_KEY:-}"
if [[ -n "${PXED_SSH_PRIVATE_KEY:-}" ]]; then
  KEY_FILE="$(mktemp "${TMPDIR:-/tmp}/pxed_deploy_key.XXXXXX")"
  CLEANUPS+=("$KEY_FILE")
  umask 077
  printf '%s\n' "$PXED_SSH_PRIVATE_KEY" > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
fi

KNOWN_FILE=""
if [[ -n "${PXED_KNOWN_HOSTS_FILE:-}" && -f "${PXED_KNOWN_HOSTS_FILE}" ]]; then
  KNOWN_FILE="$PXED_KNOWN_HOSTS_FILE"
elif [[ -n "${PXED_KNOWN_HOSTS:-}" ]]; then
  KNOWN_FILE="$(mktemp "${TMPDIR:-/tmp}/pxed_known_hosts.XXXXXX")"
  CLEANUPS+=("$KNOWN_FILE")
  printf '%s\n' "$PXED_KNOWN_HOSTS" > "$KNOWN_FILE"
  chmod 600 "$KNOWN_FILE"
fi

# CI / production: pin host keys when known_hosts provided.
# Local interactive fallback: accept-new (operator machine already trusts pxed).
if [[ -n "$KNOWN_FILE" ]]; then
  ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes
            -o UserKnownHostsFile="$KNOWN_FILE" -o GlobalKnownHostsFile=/dev/null)
  scp_opts=(-o BatchMode=yes -o StrictHostKeyChecking=yes
            -o UserKnownHostsFile="$KNOWN_FILE" -o GlobalKnownHostsFile=/dev/null)
else
  ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new)
  scp_opts=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new)
fi
if [[ -n "$KEY_FILE" ]]; then
  ssh_opts+=(-i "$KEY_FILE" -o IdentitiesOnly=yes)
  scp_opts+=(-i "$KEY_FILE" -o IdentitiesOnly=yes)
fi
if [[ "$PORT" != "22" ]]; then
  ssh_opts+=(-p "$PORT")
  scp_opts+=(-P "$PORT")
fi

# --- build / pack ---
if [[ -n "${CONSOLE10_TGZ:-}" && -f "${CONSOLE10_TGZ}" ]]; then
  TGZ="$CONSOLE10_TGZ"
  echo "[deploy] using prebuilt tarball: $TGZ"
elif [[ "${SKIP_BUILD:-0}" == "1" ]]; then
  if [[ ! -f "$ROOT/apps/web/dist/index.html" ]]; then
    echo "[deploy] SKIP_BUILD=1 but apps/web/dist/index.html missing" >&2
    exit 2
  fi
  tar -C "$ROOT/apps/web/dist" -czf "$TGZ" .
  echo "[deploy] packed existing dist → $TGZ"
else
  "$ROOT/scripts/build_web_console.sh"
  tar -C "$ROOT/apps/web/dist" -czf "$TGZ" .
fi

if [[ ! -s "$TGZ" ]]; then
  echo "[deploy] tarball missing or empty: $TGZ" >&2
  exit 2
fi

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  ls -la "$TGZ"
  echo "[deploy] DRY_RUN=1 — not copying to $HOST:$REMOTE"
  exit 0
fi

if [[ -z "${PXED_HOST:-}" && "$HOST" == "pxed" ]]; then
  : # default host alias ok for local
fi

scp "${scp_opts[@]}" "$TGZ" "$HOST:/tmp/console10-webroot.tgz"

# Remote publish: extract stage → prune stale assets (I2) → overlay → dist mirror.
# REMOTE is passed via env (not interpolated into remote shell string).
ssh -T "${ssh_opts[@]}" "$HOST" \
  env "REMOTE=$REMOTE" "PRUNE_STALE_ASSETS=$PRUNE_STALE_ASSETS" \
  bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
case "$REMOTE" in
  /data/grok-register|/data/grok-register/*) ;;
  *) echo "refusing remote path: $REMOTE" >&2; exit 1 ;;
esac
if [[ ! "$REMOTE" =~ ^/data/grok-register(/[A-Za-z0-9._+-]+)*$ ]]; then
  echo "refusing unsafe remote path: $REMOTE" >&2
  exit 1
fi
TGZ=/tmp/console10-webroot.tgz
test -s "$TGZ"
STAGE=$(mktemp -d /tmp/console10_stage.XXXXXX)
cleanup_stage() { rm -rf "$STAGE"; }
trap cleanup_stage EXIT
tar -C "$STAGE" -xzf "$TGZ"
test -f "$STAGE/index.html"

publish_webroot() {
  local DEST="$1"
  mkdir -p "$DEST"
  if [[ "${PRUNE_STALE_ASSETS:-1}" == "1" && -d "$DEST/assets" && -d "$STAGE/assets" ]]; then
    # I2: drop hashed assets no longer referenced by this build
    find "$DEST/assets" -type f -print0 2>/dev/null | while IFS= read -r -d '' f; do
      rel="${f#"$DEST/"}"
      if [[ ! -e "$STAGE/$rel" ]]; then
        rm -f "$f"
      fi
    done
    find "$DEST/assets" -type d -empty -delete 2>/dev/null || true
  fi
  # overlay new tree (index.html, assets/*, vite extras)
  cp -a "$STAGE"/. "$DEST"/
}

publish_webroot "$REMOTE"
publish_webroot "$REMOTE/dist"
rm -f "$TGZ"
echo '[deploy] web root index:'
head -12 "$REMOTE/index.html"
echo '[deploy] assets (head):'
ls -la "$REMOTE/assets" | head
REMOTE_SCRIPT

echo "[deploy] SPA on $HOST:$REMOTE (and $REMOTE/dist) — no control_api restart needed for static"
