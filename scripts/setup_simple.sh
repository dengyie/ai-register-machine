#!/usr/bin/env bash
# One-shot bootstrap for outsiders (Aaron-style: clone → config → run).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f config.json ]]; then
  if [[ -f config.simple.example.json ]]; then
    cp config.simple.example.json config.json
    echo "[ok] wrote config.json from config.simple.example.json"
  else
    cp config.example.json config.json
    echo "[ok] wrote config.json from config.example.json"
  fi
else
  echo "[skip] config.json already exists"
fi

if [[ ! -f mail_credentials.txt ]]; then
  cp mail_credentials.example.txt mail_credentials.txt
  echo "[ok] wrote mail_credentials.txt (fill 邮箱----密码----ClientID----Token)"
else
  echo "[skip] mail_credentials.txt already exists"
fi

if command -v uv >/dev/null 2>&1; then
  uv sync
  echo "[ok] uv sync done"
else
  echo "[warn] uv not found — install: https://github.com/astral-sh/uv"
  echo "       then: uv sync"
fi

cat <<'EOF'

Next:
  1) Edit config.json  → set "proxy" to your local proxy
  2) Edit mail_credentials.txt if email_provider=hotmail
  3) Register one account (headed browser recommended):

     uv run python -u register_cli.py --extra 1 --threads 1 --no-headless --fast

  4) Check outputs:

     ls accounts_cli.txt cpa_auths/
     # Product success = chat probe ok (not just models 200)
     # entitlement_denied → no free Build chat; do not remint

Full docs: README.md  |  Full config: config.example.json
EOF
