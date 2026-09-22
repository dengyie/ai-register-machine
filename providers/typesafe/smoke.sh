#!/usr/bin/env bash
# typesafe.ai smoke.
#
# Default: connectivity only (tinyhost + console login parse). No mailbox.
# Live n=1: TYPESAFE_SMOKE_LIVE=1 — COUNT=1, no CPA inject, this-run json only.
# Never treat accounts.jsonl tail as success.
set -euo pipefail

PROVIDER_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$PROVIDER_DIR/../.." && pwd)"
cd "$ROOT"

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy || true
export CPA_EXPORT_ENABLED=false
export CPA_REMOTE_INJECT=false
export CPA_PROBE_CHAT=false
export COUNT=1

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

echo "=== tinyhost ==="
curl -sS --max-time 20 -o /dev/null -w "tinyhost=%{http_code} time=%{time_total}\n" \
  https://tinyhost.shop || {
  echo "tinyhost unreachable" >&2
  exit 1
}

echo "=== console probe (no mailbox) ==="
"$PY" - <<'PY'
from providers.typesafe.protocol.flow import probe_console

info = probe_console(proxy="http://127.0.0.1:7897")
print(
    "console_probe",
    "status=" + str(info.get("status")),
    "index=" + str(info.get("index")),
    "dpl=" + str(info.get("deployment_id") or "")[:12],
)
PY

if [[ "${TYPESAFE_SMOKE_LIVE:-0}" != "1" ]]; then
  echo "SMOKE_OK (probe only; set TYPESAFE_SMOKE_LIVE=1 for n=1 register)"
  exit 0
fi

echo "=== live n=1 (this-run api_key only) ==="
mkdir -p logs providers/typesafe/output
TS=$(date +%Y%m%d_%H%M%S)
LOG="logs/typesafe_smoke_${TS}.log"
BEFORE=$("$PY" - <<'PY'
from pathlib import Path
p = Path("providers/typesafe/output")
p.mkdir(parents=True, exist_ok=True)
print(max((f.stat().st_mtime for f in p.glob("typesafe-*.json")), default=0))
PY
)

set +e
./register.sh typesafe 1 >"$LOG" 2>&1
RC=$?
set -e
echo "register_rc=$RC log=$LOG"

"$PY" - "$RC" "$LOG" "$BEFORE" <<'PY'
import json, sys
from pathlib import Path

rc = int(sys.argv[1])
log = Path(sys.argv[2]).read_text(errors="replace")
before = float(sys.argv[3])
out = Path("providers/typesafe/output")
fresh = [
    p for p in out.glob("typesafe-*.json")
    if p.stat().st_mtime >= before - 1
]
contract = None
head, sep, tail = log.rpartition("CONTRACT_EXIT:")
if sep:
    first = tail.strip().splitlines()[0].strip() if tail.strip() else ""
    try:
        contract = int(first)
    except ValueError:
        contract = None
summary = None
decoder = json.JSONDecoder()
scan = head
pos = 0
while pos < len(scan):
    brace = scan.find("{", pos)
    if brace < 0:
        break
    try:
        obj, end = decoder.raw_decode(scan, brace)
    except json.JSONDecodeError:
        pos = brace + 1
        continue
    pos = end
    if isinstance(obj, dict) and "results" in obj and "ok" in obj:
        summary = obj

ok = bool(summary and int(summary.get("ok") or 0) == 1)
kind = ""
email = ""
auth = None
if summary and summary.get("results"):
    r0 = summary["results"][0]
    kind = str(r0.get("secret_kind") or "")
    email = str(r0.get("email") or "")
if fresh:
    auth = json.loads(fresh[-1].read_text(encoding="utf-8"))

# Success = this-run secret_kind + new 0600 json. accounts.jsonl is ignored.
live_ok = (
    rc == 0
    and contract == 0
    and ok
    and kind == "api_key"
    and auth is not None
    and bool(auth.get("ok"))
    and bool(str(auth.get("api_key") or "").strip())
    and (not email or auth.get("email") == email)
)
print(json.dumps({
    "live_ok": live_ok,
    "rc": rc,
    "contract_exit": contract,
    "secret_kind": kind,
    "email": email,
    "fresh_json": str(fresh[-1]) if fresh else "",
    "accounts_jsonl_ignored": True,
}, ensure_ascii=False))
if not live_ok:
    sys.exit(1)
PY

echo SMOKE_OK
