# Web Control Plane Design

**Date:** 2026-07-21  
**Status:** Approved (conversation) — awaiting written-spec review before implementation plan  
**Product:** ai-register-machine (multi-provider register hub)

## 1. Problem

Operators run Grok/MiMo/ChatGPT registration on project hosts (local Mac or pxed VPS) via shell (`./register.sh`, `scripts/launch_batch_supervisor.sh`, import scripts). The only mature UI is a **desktop TTK** app (`GrokRegisterGUI` in `grok_register_ttk.py` ~L6044–end) that:

- Only covers Grok form + one local process thread model
- Cannot be used headless on pxed (no display)
- Duplicates config surfaces already owned by `config.json` / `.env` / CLI
- Blocks “project-internal closed loop”: control plane is not a first-class app of the monorepo

**Goal:** Replace desktop GUI with a **project-owned Web control plane** that can configure, import data, start/stop batches, and show live status — deployable on the same host that runs registration (deploy model **B**).

## 2. Non-goals (v1)

- Full job queue / cron scheduler / multi-tenant SaaS
- Mid-bulk CPA inject toggles that violate disk-first (mint path inject stays off)
- Rewriting Turnstile / browser engine / register_core migrate B (in-process Grok)
- Remote multi-host fleet orchestration (one process owns one project root)
- Desktop / Electron / Tk UI of any kind
- Auto-stopping coinbot or other unrelated services on the host
- Protocol-mint experiments unless separately requested

## 3. Locked product decisions

| Decision | Choice |
|----------|--------|
| Surface | **Web UI only** (browser) |
| Deploy | **Model B** — service belongs to the project; in-repo closed loop on the register host |
| Products | Multi-product hub: **Grok + MiMo + ChatGPT** |
| Import | Nodes/proxies, mail credentials, existing account/token dumps, full config packs |
| Auto-run tier | **Start/stop batch + realtime status** (not full auto scheduler) |
| Desktop GUI | **Delete** `GrokRegisterGUI` + docs/tests/tasks; **keep** engine in `grok_register_ttk.py` (CLI import surface) |
| Security default | Bind `127.0.0.1` + bearer token; optional LAN bind only with explicit env |
| Writes | Backup-before-write for `config.json` / known sensitive files |

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Browser (operator)                                          │
│  apps/web  — static SPA (or server-rendered minimal pages)  │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP (JSON + SSE/log tail)
┌───────────────────────────▼─────────────────────────────────┐
│ apps/control_api  (FastAPI, project-local process)          │
│  Auth: CONTROL_API_TOKEN                                    │
│  Whitelist actions only — no arbitrary shell                │
│  Project root = env REGISTER_PROJECT_ROOT or cwd discover   │
└───┬───────────────┬─────────────────┬───────────────────────┘
    │               │                 │
    ▼               ▼                 ▼
 config.json     import helpers    process control
 .env (safe)     scripts/*         supervisor flock
 profiles/       register_core     register.sh
 nodes.json      nodes import      log tail
 cpa_auths/      mail files        state.json
```

### 4.1 Principles

1. **Control plane is an adapter, not a second engine.** It does not reimplement registration. It orchestrates existing scripts/CLIs with the same env contracts production already uses.
2. **One project root per process.** Paths resolve under a single `REGISTER_PROJECT_ROOT` (default: repo root). No cross-host SSH agent in v1 (that was model A; rejected).
3. **Whitelist actions.** Every mutating endpoint maps to a named operation with validated params. No `exec(user_string)`.
4. **Disk-first honesty.** Batch start for Grok ordinary/residential uses `launch_batch_supervisor.sh` semantics: `CPA_REMOTE_INJECT=false` mid-mint; optional batch-end inject remains supervisor-owned.
5. **Fail closed on auth.** Missing token when `CONTROL_API_TOKEN` is set → 401. Prefer requiring token in production docs.
6. **Do not fight live bulk.** Control API must not `pkill` unrelated `register_cli` by name globally; stop targets the supervisor via flock pid / recorded run pid only.

### 4.2 Components

| Component | Path | Responsibility |
|-----------|------|----------------|
| Control API | `apps/control_api/` | FastAPI app, auth middleware, route modules, process manager, config IO |
| Web frontend | `apps/web/` | Operator UI: Overview, Config, Import, Runs |
| Shared types (optional thin) | `apps/control_api/schemas.py` | Pydantic request/response models consumed by OpenAPI |
| Docs | `apps/README.md`, root README/ARCHITECTURE | Replace desktop GUI entries with Web control plane |

### 4.3 Process model

- Start: `uv run python -m apps.control_api` (or `scripts/run_control_api.sh`) with `REGISTER_PROJECT_ROOT`, `CONTROL_API_TOKEN`, `CONTROL_API_HOST=127.0.0.1`, `CONTROL_API_PORT=8787`.
- Frontend: v1 ships **static HTML/CSS/vanilla JS** (optional HTMX via CDN only) served by FastAPI `StaticFiles` at `/`. **No Node/npm build** on pxed (2 vCPU / ~3.8G).
- Single worker. Uvicorn one process is enough; batch work is external subprocesses.

### 4.4 Why not alternatives (record)

| Approach | Verdict |
|----------|---------|
| A. Mac control plane + SSH agent to pxed | Rejected — not project-closed-loop |
| C. Grow TTK desktop | Rejected — no desktop GUI |
| B. In-project Web service | **Chosen** |

## 5. Pages & UX

Four pages (single-page shell with nav):

### 5.1 Overview

- Project root, host bind, product counts (`cpa_auths/xai-*.json` complete, optional MiMo/ChatGPT sinks if present)
- Live run card: running? tag, mode, complete/goal, consecutive_zero, pid, last supervisor line
- Quick links: open Config / Import / Runs
- Health: Clash mixed-port optional probe (read-only; may be skipped if `SKIP_CLASH_PREFLIGHT`)

### 5.2 Config

Human-editable subset of `config.json` + safe display of related env:

**Editable groups (v1):**

- Mail: `email_provider` / `email_providers`, `defaultDomains`, provider-specific non-secret fields (URLs, paths)
- Network: `proxy`, `proxy_rotate_mode`, `proxy_rotate_every`, `clash_proxy_group`, `proxy_list` path ref (not pasting thousands of lines into DOM by default — file path + “upload replace”)
- Batch/engine knobs already in config: `turnstile_stuck_timeout`, headless-related flags if present, `cpa_export_enabled`, `cpa_probe_chat` (default show false for bulk honesty), `cpa_remote_inject` as **intent** only (document: supervisor freezes mid-mint inject)
- Register counts defaults if stored in config

**Secrets policy:**

- Never echo raw secrets back in full after save (mask `***` + last 4)
- Gmail app password / API keys: write-only fields; empty submit = leave unchanged
- `.env` edits: v1 allowlist keys only (`DEFAULT_DOMAINS`, `EMAIL_PROVIDER`, `CPA_*` safe flags, `PROXY_LIST` path) — not free-form dump of entire `.env` in browser

**Write path:**

1. Validate JSON types / enums server-side  
2. `config.json.bak-web-<timestamp>` copy  
3. Atomic replace write  
4. Return diff summary (keys changed, secrets redacted)

### 5.3 Import

Four import types (tabs):

| Type | Backend action | Notes |
|------|----------------|-------|
| Nodes / proxies | Wrap `scripts/import_nodes.py` / `python -m register_core nodes import` | Upload file to temp under project `output/web_uploads/`, then import; support dry-run flag |
| Mail credentials | Append/replace `mail_credentials.txt` (or configured path) with backup | Format check line pattern; no plus-alias farm promotion |
| Account / token dumps | Wrap `scripts/import_cpa_auth_dir.py` | Default **local disk only** (`--no-remote`) unless operator explicitly enables remote inject; healthy-only remains script law |
| Config pack | Zip or multi-file: `config.json` + optional `nodes.json` + mail file | Extract to staging, show plan, apply with backups |

All imports return structured result: counts, errors, paths touched, dry_run bool.

### 5.4 Runs

- **Start batch (Grok bulk):** mode `ordinary|residential`, target N, threads (default 1), tag prefix; maps to `scripts/launch_batch_supervisor.sh`  
- **Start one-shot (any product):** `./register.sh <product> N [threads]` with env allowlist (e.g. `SKIP_CLASH_PREFLIGHT` checkbox)  
- **Stop:** send SIGTERM to recorded supervisor PID (from lock pid file `/tmp/grok_batch_supervisor.lock.pid` or run registry); wait; if still alive SIGKILL after grace; **never** blanket `pkill -f register_cli`  
- **Status:** poll `GET /api/runs/current` — pid alive?, complete, goal, zero streak, last log lines  
- **Log tail:** `GET /api/runs/current/logs?tail=200` or SSE stream of supervisor log

**Concurrency:** at most one Grok supervisor (existing flock). API returns 409 if flock held and start requested.

**MiMo/ChatGPT runs:** v1 start via `./register.sh mimo|chatgpt` one-shot only (no long supervisor parity unless already exists). Status = child process registry entry until exit.

## 6. API contract (v1)

Base: `/api`. Auth: `Authorization: Bearer <CONTROL_API_TOKEN>` (or header `X-Control-Token`).

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | liveness; no auth optional for local probes |
| GET | `/api/overview` | counts + current run summary |
| GET | `/api/config` | redacted config view + schema hints |
| PUT | `/api/config` | validated partial/full update + backup |
| POST | `/api/import/nodes` | multipart file + options |
| POST | `/api/import/mail` | multipart or text body |
| POST | `/api/import/auths` | path or upload dir zip; flags |
| POST | `/api/import/pack` | config pack apply |
| GET | `/api/runs` | recent runs from registry/logs index |
| GET | `/api/runs/current` | live supervisor/one-shot state |
| POST | `/api/runs/start` | body: `{kind, product?, mode?, target?, threads?, tag?, env?}` |
| POST | `/api/runs/stop` | stop current controlled run |
| GET | `/api/runs/current/logs` | tail query |

### 6.1 Start body (normative)

```json
{
  "kind": "grok_supervisor" | "register_sh",
  "product": "grok" | "mimo" | "chatgpt",
  "mode": "ordinary" | "residential",
  "target": 100,
  "threads": 1,
  "tag": "batch_web",
  "extra_env": {
    "SKIP_CLASH_PREFLIGHT": "0"
  }
}
```

`extra_env` keys must be in server allowlist. Unknown keys → 400.

### 6.2 Errors

- `400` validation  
- `401` auth  
- `409` supervisor already running / lock held  
- `422` import format  
- `500` subprocess failure with captured stderr tail (redact secrets)

## 7. Desktop GUI deletion inventory

### 7.1 Delete / strip

| Item | Action |
|------|--------|
| `grok_register_ttk.py` `class GrokRegisterGUI` (~6044–7358) | **Delete class body** |
| `def main()` + `if __name__ == "__main__"` Tk launch | Replace with message: “Desktop GUI removed; use Web control plane / CLI” exit 2, **or** remove `__main__` GUI entry entirely |
| Top-level `import tkinter` / `ttk` / `messagebox` / `scrolledtext` | Remove if only used by GUI |
| `tests/unit/test_gui_layout_helpers.py` | Delete |
| `apps/gui/README.md` | Replace with pointer to Web control plane or delete dir |
| `apps/README.md` Grok GUI row | Replace with control_api + web |
| Root `README.md` desktop UI sections | Replace with Web UI run instructions |
| `ARCHITECTURE.md` desktop GUI / backlog Web UI | Web is the UI; remove “desktop TTK mature” backlog line |
| `mise.toml` `[tasks.gui]` | Replace with `control-api` task |
| `pyproject.toml` description/keywords `gui` | Retarget to web control plane |
| Any CONTRIBUTING mentions of Tk GUI | Update |

### 7.2 Keep (hard)

| Item | Why |
|------|-----|
| All engine functions in `grok_register_ttk.py` above GUI class | `register_cli.py` and email sources import them |
| `register_cli.py` import of `grok_register_ttk as reg` | Production path |
| Non-GUI tests (`test_email_providers_pool`, hotmail, gmail, etc.) | Engine regression |
| `register_core` adapters shell-out targets | Unchanged |

### 7.3 Verification after deletion

1. `python -m py_compile grok_register_ttk.py register_cli.py`  
2. `pytest -q` — GUI test gone; all others green  
3. `rg -n "GrokRegisterGUI|tkinter" --glob '*.py'` → empty (or only historical docs if any)  
4. Smoke: `register_cli --help` still loads module

## 8. Security & ops

- Default bind **127.0.0.1**. Document SSH tunnel / reverse proxy if remote browser needed.  
- Token required when set; generate with `openssl rand -hex 32`.  
- Upload size cap (e.g. 20MB) and staging dir under project `output/web_uploads/` (gitignored).  
- Log redaction helper for tokens in API error paths.  
- Do not expose raw `mail_credentials.txt` or full auth JSON via GET list without explicit operator action; overview counts only.  
- Control API must not change global host proxy or stop coinbot.

## 9. Testing strategy

| Layer | What |
|-------|------|
| Unit | Config backup/write, redaction, env allowlist, start body validation |
| Unit | Process registry: stop only recorded pid; 409 when lock held (mock flock/pid) |
| Unit | Import path traversal rejected (`../` uploads) |
| API integration | TestClient: health, config get/put roundtrip with tmp project root |
| Regression | Existing pytest suite after GUI deletion |
| Manual | On Mac: start API, open UI, dry-run nodes import, start `register.sh grok 0/1` dry if available; **do not** attach to live `batch_dc1k_ns` stop without operator intent |

## 10. Rollout

1. Land control_api + static web + tests on main (feature complete offline).  
2. Delete desktop GUI in same or immediately following change set (same milestone — user asked to remove desktop).  
3. Docs/mise/pyproject update.  
4. Optional deploy on pxed: run control_api under existing venv; browser via SSH tunnel. **Do not** auto-restart or stop `batch_dc1k_ns` as part of deploy.  
5. Memory/ARCHITECTURE: Web control plane is production UI surface.

## 11. Success criteria

- Operator can, from a browser on the project host (or tunnel): edit safe config, import nodes/mail/auths/pack, start/stop a supervised Grok batch or one-shot product run, see complete/goal and log tail.  
- No Tk/desktop entry remains.  
- `register_cli` / engine imports still work; pytest green.  
- Live bulk disk-first contracts unchanged (inject off mid-mint).  
- pxed resource: control API idle footprint small (single uvicorn, static files).

## 12. Implementation notes for planning (not code)

- Prefer package layout:

```
apps/control_api/
  __init__.py
  __main__.py
  app.py           # FastAPI factory
  auth.py
  config_io.py
  imports.py
  runs.py
  process_registry.py
  schemas.py
apps/web/
  index.html
  assets/...
```

- Reuse subprocess patterns from existing scripts; do not re-parse Clash YAML in the API.  
- Supervisor state already writes `logs/<tag>.state.json` and supervisor log — prefer reading those over scraping ps.  
- Frontend: one HTML shell + fetch API; keep CSS minimal and readable in terminal browsers not required.

## 13. Open points resolved in design

| Topic | Resolution |
|-------|------------|
| SPA framework | None required — static + vanilla JS/HTMX |
| Multi-host | Out of v1 |
| Scheduler | Out of v1 |
| CPA mid-batch inject UI | Forbidden; intent flag only |
| GUI deletion vs engine file | Surgical delete of class + tk imports; keep engine |

## 14. Approval trail

- Architecture §1 (project-owned FastAPI + web, multi-product, import four types, start/stop+status, delete desktop GUI): **user approved** (“认可，可以做了”).  
- This document formalizes §1–§3 (pages/API + GUI deletion inventory).  
- Next: user review of this file → `writing-plans` → implement.
