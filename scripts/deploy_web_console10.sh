#!/usr/bin/env bash
# scripts/deploy_web_console10.sh — MANUAL or CI; does not stop batch/coinbot
# Build (unless SKIP_BUILD=1 / CONSOLE10_TGZ set), scp SPA to pxed.
#
# control_api mounts StaticFiles at apps/web (NOT apps/web/dist):
#   web_dir = .../apps/web  + index.html + assets/
# so we publish dist/* into that web root, and also keep apps/web/dist in sync.
#
# Env:
#   PXED_HOST          ssh target (default: pxed)
#   PXED_WEB           remote web root (default: /data/grok-register/apps/web)
#                      must stay under /data/grok-register (sanitized)
#   PXED_SSH_PORT      default 22
#   PXED_SSH_KEY       optional private key path (IdentitiesOnly)
#   SKIP_BUILD=1       use existing apps/web/dist (or CONSOLE10_TGZ)
#   CONSOLE10_TGZ      prebuilt tarball of dist contents; skips local build
#   DRY_RUN=1          pack only; no scp/ssh
set -euo pipefail
export COPYFILE_DISABLE=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${PXED_HOST:-pxed}"
REMOTE="${PXED_WEB:-/data/grok-register/apps/web}"
PORT="${PXED_SSH_PORT:-22}"
TGZ="${CONSOLE10_TGZ:-/tmp/console10-webroot.tgz}"

# Sanitize remote path (same policy as .github/workflows/deploy.yml)
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

ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new)
scp_opts=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new)
if [[ -n "${PXED_SSH_KEY:-}" ]]; then
  ssh_opts+=(-i "$PXED_SSH_KEY" -o IdentitiesOnly=yes)
  scp_opts+=(-i "$PXED_SSH_KEY" -o IdentitiesOnly=yes)
fi
if [[ "$PORT" != "22" ]]; then
  ssh_opts+=(-p "$PORT")
  scp_opts+=(-P "$PORT")
fi

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

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  ls -la "$TGZ"
  echo "[deploy] DRY_RUN=1 — not copying to $HOST:$REMOTE"
  exit 0
fi

scp "${scp_opts[@]}" "$TGZ" "$HOST:/tmp/console10-webroot.tgz"
# Pass REMOTE via env so it is not re-interpolated into a shell string.
ssh "${ssh_opts[@]}" "$HOST" \
  env "REMOTE=$REMOTE" \
  bash -s <<'REMOTE_SCRIPT'
set -euo pipefail
case "$REMOTE" in
  /data/grok-register|/data/grok-register/*) ;;
  *) echo "refusing remote path: $REMOTE" >&2; exit 1 ;;
esac
mkdir -p "$REMOTE/assets" "$REMOTE/dist"
# live static root served by control_api
tar -C "$REMOTE" -xzf /tmp/console10-webroot.tgz
# keep dist mirror too
tar -C "$REMOTE/dist" -xzf /tmp/console10-webroot.tgz
rm -f /tmp/console10-webroot.tgz
echo '[deploy] web root index:'
head -12 "$REMOTE/index.html"
ls -la "$REMOTE/assets" | head
REMOTE_SCRIPT
echo "[deploy] SPA on $HOST:$REMOTE (and $REMOTE/dist) — no control_api restart needed for static"
