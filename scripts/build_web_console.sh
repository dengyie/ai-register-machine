#!/usr/bin/env bash
# scripts/build_web_console.sh — build console10 (Vite+Preact) into apps/web/dist
# Prefer npm ci when package-lock.json is present (CI-friendly).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/apps/web"
if [[ -f package-lock.json ]]; then
  npm ci
elif [[ ! -d node_modules ]]; then
  npm install
fi
npm run build
test -f dist/index.html
echo "[web] built → $ROOT/apps/web/dist"
