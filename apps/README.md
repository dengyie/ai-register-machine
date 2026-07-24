# Apps (entrypoints)

Thin map of user-facing entrypoints. Implementation may still live at repo root.

| App | How to run | Notes |
|-----|------------|--------|
| **Hub** | `./register.sh help` | Multi-provider |
| **Grok CLI** | `./register.sh grok 1 1` | Production |
| **Web control plane** | `./scripts/run_control_api.sh` | Config / Import / Runs UI at `http://127.0.0.1:8787` |
| **MiMo** | `./register.sh mimo` | Node runtime |
| **Core** | `./register.sh core list` | Layered orchestration |

## Web control plane

```bash
export REGISTER_PROJECT_ROOT="$(pwd)"   # optional; defaults to cwd
export CONTROL_API_HOST=127.0.0.1
export CONTROL_API_PORT=8787
# Recommended: stable session secret + optional script bearer
export CONTROL_API_SESSION_SECRET="$(openssl rand -hex 32)"
export CONTROL_API_TOKEN="$(openssl rand -hex 32)"   # optional; for curl/scripts
# First start with empty user store auto-creates:
#   username: admin
#   password: admin123
# Override: CONTROL_API_BOOTSTRAP_USER / CONTROL_API_BOOTSTRAP_PASSWORD
# Or set/change later:
#   uv run python scripts/control_api_user.py set admin
./scripts/run_control_api.sh
```

Open `http://127.0.0.1:8787` → **login form**. Default credentials **`admin` / `admin123`** when `.control_api_users.json` does not exist yet. Session is HttpOnly cookie.

- API: `/api/health`, `/api/auth/*`, `/api/overview`, `/api/config`, `/api/import/*`, `/api/runs/*`, `/api/nodes/*`, `/api/accounts/*`, `/api/ops/*`
- UI: **console10** Preact SPA — `cd apps/web && npm run build` (or `./scripts/build_web_console.sh`) writes `apps/web/dist`; FastAPI prefers `apps/web/dist` then falls back to flat `apps/web/`.
  - **Packaging (preferred):** GitHub Actions `CI` job `package-console10` builds on every green test and uploads artifact `console10-web` (14d).
  - **Deploy (preferred):** Actions → **Deploy console10** (`workflow_dispatch` only, Environment `pxed`). Needs secrets `PXED_SSH_PRIVATE_KEY` + `PXED_HOST` + `PXED_KNOWN_HOSTS` (optional `PXED_WEB`, `PXED_SSH_PORT`). Prefer direct host (not cloudflared alias). `remote_web` is sanitized under `/data/grok-register`. Static-only; does **not** stop batch/coinbot. `dry_run` builds/downloads without secrets.
  - **Local fallback:** `./scripts/deploy_web_console10.sh` (manual scp; same remote layout). `SKIP_BUILD=1` / `CONSOLE10_TGZ=` / `DRY_RUN=1` supported.
  - Dev: `cd apps/web && npm run dev` proxies `/api` → `:8787`. Legacy console9 under `apps/web/legacy/`.
- Auth (either):
  - **Browser:** password login → signed cookie `control_session`
  - **Scripts:** `Authorization: Bearer <CONTROL_API_TOKEN>` or `X-Control-Token`
- Operators file: `.control_api_users.json` (gitignored, mode 0600, scrypt hashes). Bootstrap is a **no-op** if this file already has users (production stays as configured).
- Login rate limit: 8 failures / 5 min per IP+username
- Default bind is localhost; use SSH tunnel for remote browser access
- Disable password login only for break-glass: `CONTROL_API_PASSWORD_LOGIN=0` (then rely on bearer or open)

Subdir `gui/` only documents that the desktop TTK UI was removed.
