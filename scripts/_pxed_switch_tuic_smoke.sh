#!/usr/bin/env bash
# Run on pxed: select GVPS-TUIC-googlevps (user's TUIC-googlevps) and verify egress.
#
# EGRESS_IP must use scripts/check_clash_egress.py (curl_cffi/urllib), NOT bare
# `curl -x 7897` — on Bohrium/pxed system curl can mis-report host CN IP.
set -u
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy NO_PROXY no_proxy
export http_proxy= https_proxy= HTTP_PROXY= HTTPS_PROXY= ALL_PROXY= all_proxy= NO_PROXY= no_proxy=

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ ! -d "$ROOT/register_core" && -d /personal/grok-register/register_core ]]; then
  ROOT=/personal/grok-register
fi
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY=python3
fi
EGRESS_CHECK="${ROOT}/scripts/check_clash_egress.py"

NODE_CANDIDATES=("TUIC-googlevps" "GVPS-TUIC-googlevps")
CFG=/personal/clash/config.mac-merged.yaml
NODE=""
for n in "${NODE_CANDIDATES[@]}"; do
  if grep -qE "^[[:space:]]*-[[:space:]]*name:[[:space:]]*[\"']?${n}[\"']?[[:space:]]*$" "$CFG" \
    || grep -qE "^[[:space:]]*name:[[:space:]]*[\"']?${n}[\"']?[[:space:]]*$" "$CFG"; then
    NODE="$n"
    break
  fi
done
if [ -z "$NODE" ]; then
  # fallback: first *TUIC*googlevps* name line
  NODE=$(python3 - <<'PY'
from pathlib import Path
import re
t = Path("/personal/clash/config.mac-merged.yaml").read_text(errors="ignore")
for line in t.splitlines():
    m = re.match(r"\s*-?\s*name:\s*['\"]?((?:GVPS-)?TUIC-googlevps)['\"]?\s*$", line)
    if m:
        print(m.group(1))
        break
else:
    print("GVPS-TUIC-googlevps")
PY
)
fi
echo "RESOLVED_NODE=$NODE"
export GROK_NODE="$NODE"
export GROK_CONFIG="$CFG"
bash "${ROOT}/start-clash-for-grok.sh" || exit 1

SECRET=$(cat /personal/clash/.controller-secret)
echo -n "mihomo version: "
curl --noproxy '*' -sS -H "Authorization: Bearer $SECRET" http://127.0.0.1:9090/version
echo

python3 - <<PY
import json, urllib.request
req = urllib.request.Request(
    "http://127.0.0.1:9090/proxies",
    headers={"Authorization": "Bearer ${SECRET}"},
)
with urllib.request.urlopen(req, timeout=10) as r:
    d = json.load(r)
names = sorted(d.get("proxies", {}).keys())
print("total_proxies", len(names))
for n in names:
    if "TUIC" in n.upper() or "googlevps" in n.lower() or n in ("GLOBAL", "PROXY") or "Grok" in n or "ChatGPT" in n:
        p = d["proxies"][n]
        print(f"{n} type={p.get('type')} now={p.get('now')}")
if "${NODE}" not in d.get("proxies", {}):
    print("ERROR: node not in runtime proxies:", "${NODE}")
    raise SystemExit(2)
PY

# Pin same register groups as start-clash / probe_clash_nodes.
GPATH=$(python3 -c "import urllib.parse; print(urllib.parse.quote('🎯Grok注册'))")
CPATH=$(python3 -c "import urllib.parse; print(urllib.parse.quote('🔰ChatGPT'))")
for g in GLOBAL PROXY "$GPATH" "$CPATH"; do
  code=$(curl --noproxy '*' -sS -o /tmp/sel.json -w "%{http_code}" \
    -X PUT -H "Authorization: Bearer $SECRET" -H "Content-Type: application/json" \
    "http://127.0.0.1:9090/proxies/$g" \
    -d "{\"name\":\"$NODE\"}")
  echo "select $g -> $code $(cat /tmp/sel.json 2>/dev/null || true)"
done

for g in GLOBAL PROXY "$GPATH" "$CPATH"; do
  now=$(curl --noproxy '*' -sS -H "Authorization: Bearer $SECRET" \
    "http://127.0.0.1:9090/proxies/$g" | python3 -c "import sys,json; print(json.load(sys.stdin).get('now'))")
  echo "group=$g now=$now"
done

echo -n "delay: "
curl --noproxy '*' -sS -H "Authorization: Bearer $SECRET" \
  "http://127.0.0.1:9090/proxies/$(python3 -c "import urllib.parse; print(urllib.parse.quote('''$NODE'''))")/delay?timeout=8000&url=http://www.gstatic.com/generate_204"
echo

# Authoritative egress (never bare curl -x on pxed).
if [[ -f "$EGRESS_CHECK" ]]; then
  "$PY" "$EGRESS_CHECK" --proxy http://127.0.0.1:7897 --timeout 25 || {
    echo "EGRESS_IP=FAIL (check_clash_egress.py)" >&2
    exit 2
  }
else
  # Inline fallback when script not yet scp'd: same stack as health.probe_egress_ip
  "$PY" - <<'PY' || exit 2
import sys
from pathlib import Path
sys.path.insert(0, str(Path("/personal/grok-register")))
from register_core.nodes.health import probe_egress_ip
r = probe_egress_ip("http://127.0.0.1:7897", timeout=25.0)
if r.get("ok"):
    print(f"EGRESS_IP={r['ip']} backend={r.get('backend')} ms={r.get('ms')}")
    raise SystemExit(0)
print(f"EGRESS_IP=FAIL error={r.get('error')}", file=sys.stderr)
raise SystemExit(2)
PY
fi

# Connectivity probes may still use curl for HTTP status only (not for IP identity).
echo -n "accounts.x.ai "
curl --noproxy '*' -sS -x http://127.0.0.1:7897 --max-time 30 -o /dev/null \
  -w "%{http_code} time=%{time_total}\n" https://accounts.x.ai/ || true
echo -n "cli-chat-proxy "
curl --noproxy '*' -sS -x http://127.0.0.1:7897 --max-time 30 -o /dev/null \
  -w "%{http_code} time=%{time_total}\n" https://cli-chat-proxy.grok.com/v1/models || true
echo "SWITCH_OK node=$NODE"
