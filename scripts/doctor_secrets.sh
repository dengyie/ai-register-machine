#!/usr/bin/env bash
# Local secret hygiene check — never prints file contents.
# Exit 0 = clean for git hygiene; 1 = tracked-secret or hard fail; 2 = warnings only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WARN=0
HARD=0
ok() { echo "[ok] $*"; }
warn() { echo "[warn] $*"; WARN=1; }
fail() { echo "[fail] $*"; HARD=1; }

echo "=== doctor_secrets (paths only, no content) ==="

# 1) Must not be tracked by git
TRACKED_BAD="$(
  git ls-files 2>/dev/null | grep -E \
    '(^|/)(config\.json|\.env|mail_credentials\.txt|accounts_cli\.txt)$|mail_assets/hotmail|cpa_auths/xai-|^backups/|^logs/' \
    || true
)"
if [[ -n "$TRACKED_BAD" ]]; then
  fail "tracked secret/runtime paths:"
  echo "$TRACKED_BAD" | sed 's/^/  /'
else
  ok "no secret/runtime paths tracked by git"
fi

# 2) Example templates must exist and not be the live secret names only
for f in config.example.json config.simple.example.json mail_credentials.example.txt .env.example; do
  if [[ -f "$f" ]]; then
    ok "template present: $f"
  else
    warn "missing template: $f"
  fi
done

# 3) Live secret files: prefer mode 600 when present
check_mode() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    return 0
  fi
  if [[ ! -f "$path" ]]; then
    ok "present (dir): $path"
    return 0
  fi
  local mode
  mode="$(stat -f '%OLp' "$path" 2>/dev/null || stat -c '%a' "$path" 2>/dev/null || echo '?')"
  # last 3 digits; warn if group/other readable
  if [[ "$mode" =~ ^[0-9]+$ ]]; then
    local last3=$((10#$mode % 1000))
    local go=$((last3 % 100))
    if (( go != 0 )); then
      warn "$path mode=$mode (recommend chmod 600; group/other bits set)"
    else
      ok "$path mode=$mode"
    fi
  else
    ok "$path present (mode unknown)"
  fi
}

check_mode config.json
check_mode .env
check_mode mail_credentials.txt
check_mode accounts_cli.txt

# 4) Runtime dirs size hints (counts only)
if [[ -d cpa_auths ]]; then
  n="$(find cpa_auths -maxdepth 1 -name 'xai-*.json' 2>/dev/null | wc -l | tr -d ' ')"
  ok "cpa_auths xai-*.json count=$n"
fi
if [[ -d backups ]]; then
  ok "backups/ present (gitignored)"
fi
if [[ -d logs ]]; then
  ok "logs/ present (gitignored)"
fi

# 5) Cloud sync risk (best-effort)
case "$ROOT" in
  *"/Library/Mobile Documents/"*|*"/iCloud"*|*"/Dropbox"*|*"/Google Drive"*|*"/OneDrive"*)
    warn "repo path looks cloud-synced — keep mail_credentials/cpa_auths off sync or use encrypted disk"
    ;;
esac

# 6) Accidental force-add patterns in index (staged)
STAGED_BAD="$(
  git diff --cached --name-only 2>/dev/null | grep -E \
    '(^|/)(config\.json|\.env|mail_credentials\.txt|accounts_cli\.txt)$|cpa_auths/xai-|^backups/|^logs/' \
    || true
)"
if [[ -n "$STAGED_BAD" ]]; then
  fail "staged secret paths (unstage before commit):"
  echo "$STAGED_BAD" | sed 's/^/  /'
fi

echo "=== end doctor_secrets ==="

if [[ "$HARD" -ne 0 ]]; then
  exit 1
fi
if [[ "$WARN" -ne 0 ]]; then
  exit 2
fi
exit 0
