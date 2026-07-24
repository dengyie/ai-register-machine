# Mail Pool Probe (邮箱验证码/凭据探测) Design

**Date:** 2026-07-24  
**Status:** Approved for planning  
**Goal:** Let operators sample-test Hotmail/Outlook (and future domain) credentials in the control console: mark good ones as usable, and optionally quarantine dead ones out of the live pool.

---

## 1. Problem

The CPA register machine keeps a large Microsoft mail pool (`mail_credentials.txt`, ~64k lines, format `email----password----client_id----refresh_token`). Many refresh tokens are expired or accounts are in Microsoft abuse mode. There is:

- No first-class way to **probe** pool liveliness from the console
- No safe **remove/archive** path for dead credentials (tokens stay in the pool until hand-edited)
- No domain-aware filter (Hotmail vs Outlook vs future domains)

Operators need a page feature + a CLI script that answers: “is this mailbox’s OAuth still alive?” and “move the dead ones out.”

---

## 2. Scope

### In scope

1. **Probe depth:** OAuth refresh only (`refresh_token` → `access_token`). Success = “好用”. No real OTP send/receive.
2. **Domain filter:** Filter pool by email domain (`hotmail.com`, `outlook.com`, …). Multi-select; extensible for future domains.
3. **Quarantine:** On user confirm, remove selected dead emails from the main pool and append them to a dead archive file (with reason + timestamp). Backup main pool before rewrite.
4. **Surfaces:**
   - Shared pure logic module
   - CLI: `scripts/probe_mail_pool.py`
   - control_api endpoints
   - Web: Resources → 邮箱 (`MailTab`)

### Out of scope (YAGNI)

- End-to-end OTP (send mail + wait for code)
- Graph/REST “list recent mail” second stage
- Full-pool single-shot scan of ~64k accounts
- Async job queue / long-running background probe with cancel
- Changing register-time load logic to skip “marked dead” lines (dead are **physically removed** from main pool)
- Returning password / client_id / refresh_token to the browser

---

## 3. Architecture

Three layers, one shared core:

```
┌─────────────────────────────────────────────┐
│  apps/web MailTab (Resources → 邮箱)         │
└───────────────────┬─────────────────────────┘
                    │ HTTP (session / token)
┌───────────────────▼─────────────────────────┐
│  control_api routes_mail + mail_ops         │
└───────────────────┬─────────────────────────┘
                    │ import
┌───────────────────▼─────────────────────────┐
│  mail_pool_probe.py  (pure logic)           │
│  load · filter · probe · classify · archive │
└───────────────────┬─────────────────────────┘
                    ▲
┌───────────────────┴─────────────────────────┐
│  scripts/probe_mail_pool.py  (CLI)          │
└─────────────────────────────────────────────┘
```

### Module placement

| Piece | Path | Responsibility |
|-------|------|----------------|
| Core | `mail_pool_probe.py` (repo root, next to other ops helpers) | Parse pool, domain filter, sample, OAuth refresh wrapper, status classify, quarantine rewrite |
| CLI | `scripts/probe_mail_pool.py` | argparse: domains, limit, seed, concurrency, optional quarantine list / dry-run |
| API ops | `apps/control_api/mail_ops.py` | Thin adapter: resolve paths from settings/config, call core, shape responses |
| API routes | `apps/control_api/routes_mail.py` | `GET pool`, `POST probe`, `POST quarantine` |
| Schemas | `apps/control_api/schemas.py` | Request/response models (no secrets) |
| UI | `apps/web/src/pages/Resources/MailTab.jsx` | KPI + probe form + result table + quarantine action |
| Client | `apps/web/src/api/client.js` | `mailPoolStats`, `probeMail`, `quarantineMail` |
| Tests | `tests/unit/test_mail_pool_probe.py` (+ control_api tests as needed) | Domain filter, classify, quarantine rewrite with temp files; mock refresh |

**Why root `mail_pool_probe.py` (not inside `grok_register_ttk.py`):** ttk is heavy (browser/GUI side effects). Core must stay import-safe for FastAPI. Refresh implementation may **call into** existing `hotmail_refresh_access_token` when available, or reimplement a minimal OAuth refresh using the same endpoints/constants, behind a small injectable callable for tests.

---

## 4. Data model & files

### Live pool

- Path: config `hotmail_accounts_file` / env `HOTMAIL_ACCOUNTS_FILE` / default `mail_credentials.txt`
- Line: `email----password----client_id----refresh_token`
- Dedupe key: lowercased email

### Dead archive

- Path: sibling of live pool, fixed name `mail_credentials.dead.txt` (same directory as live file), unless overridden later
- Append-only line:
  ```
  email----password----client_id----refresh_token----reason----iso_ts
  ```
  (`reason` is a short class code or truncated MS error; `iso_ts` is UTC ISO-8601)

### Backup

- Before any rewrite of the live pool: copy to `mail_credentials.txt.bak-probe-YYYYMMDD_HHMMSS` (same pattern spirit as web import backups)

### Probe result row (API/UI, no secrets)

```json
{
  "email": "user@hotmail.com",
  "domain": "hotmail.com",
  "status": "ok",
  "reason": "",
  "ms_error": ""
}
```

`status` enum:

| status | meaning | UI label |
|--------|---------|----------|
| `ok` | refresh succeeded | 好用 |
| `grant_expired` | AADSTS grant expired | 挂了 |
| `refresh_invalid` | refresh token invalid | 挂了 |
| `abuse_mode` | service abuse mode | 挂了 |
| `network_error` | timeout / connection | 挂了 |
| `parse_error` | bad line / missing fields | 挂了 |
| `unknown` | other non-success | 挂了 |

Classification maps Microsoft error text (e.g. `AADSTS70000`, “grant is expired”, “abuse mode”, “not valid”) into the above codes. Raw message may be stored truncated in `ms_error` (max ~200 chars) for operator context — never tokens.

---

## 5. API

Auth: same as other control routes (`require_auth` — session cookie, Bearer, or `X-Control-Token`).

### `GET /api/mail/pool`

Pool statistics only.

**Response:**
```json
{
  "path": "mail_credentials.txt",
  "total": 64179,
  "by_domain": {
    "hotmail.com": 64177,
    "outlook.com": 2
  },
  "dead_path": "mail_credentials.dead.txt",
  "dead_total": 0,
  "known_domains": ["hotmail.com", "outlook.com", "live.com", "msn.com"]
}
```

Never returns credentials. `known_domains` is a static/extensible list for UI chips; counts only include domains present in the file (plus an `"other"` bucket if needed).

### `POST /api/mail/probe`

**Request:**
```json
{
  "domains": ["hotmail.com", "outlook.com"],
  "limit": 30,
  "seed": null,
  "concurrency": 4
}
```

| field | rules |
|-------|--------|
| `domains` | non-empty list; match email domain case-insensitively; empty/omitted = all domains |
| `limit` | default **30**, min 1, max **200** |
| `seed` | optional int for reproducible sampling |
| `concurrency` | default **4**, min 1, max **8** |

**Response:**
```json
{
  "probed": 30,
  "ok": 14,
  "dead": 16,
  "by_status": {"ok": 14, "grant_expired": 16},
  "results": [ /* ProbeResult rows */ ]
}
```

Sampling: among lines whose domain is in `domains`, choose up to `limit` accounts (deterministic if `seed` set). Do not load/probe the entire 64k in one request.

### `POST /api/mail/quarantine`

**Request:**
```json
{
  "emails": ["a@hotmail.com", "b@outlook.com"],
  "reason": "probe:grant_expired"
}
```

| field | rules |
|-------|--------|
| `emails` | **required**, non-empty, explicit list (no server-side “all failures from last probe”) |
| `reason` | optional tag stored in dead file; default `quarantine` |

**Behavior:**
1. Resolve live pool path; read all lines
2. Backup live file
3. Split: matched emails → dead archive (append, with reason + ts); remainder → rewrite live file
4. Return counts; force any in-process mtime cache invalidation if applicable

**Response:**
```json
{
  "removed": 2,
  "not_found": 0,
  "dead_path": "mail_credentials.dead.txt",
  "backup_path": "mail_credentials.txt.bak-probe-20260724_153012",
  "live_total_after": 64177
}
```

---

## 6. CLI

```bash
python scripts/probe_mail_pool.py \
  --domains hotmail.com,outlook.com \
  --limit 30 \
  --seed 20260724 \
  --concurrency 4 \
  [--json] \
  [--quarantine-dead]   # optional: after probe, archive all non-ok from THIS run (CLI-only convenience; API still requires explicit list)
```

- Default: print human summary + table
- `--json`: machine-readable results
- Exit code: 0 if ran; non-zero on hard failure (file missing, etc.), not merely because some accounts are dead

---

## 7. UI (Resources → 邮箱)

Extend existing `MailTab` (do not add a new top-level nav item).

### Blocks (top → bottom)

1. **KPI strip** — live total; per-domain counts; dead archive count  
2. **Probe controls**
   - Domain multi-select chips (from `known_domains` / `by_domain` keys)
   - Limit number input (default 30, max 200)
   - Primary button: 「开始探测」
   - While running: disable controls; show `done/total` style progress if available (single request → simple busy state is enough)
3. **Results table**
   - Columns: checkbox · email · domain · status (好用/挂了) · reason/ms_error
   - Default: auto-check all non-`ok` rows
   - Actions: 「全选挂了」· 「取消勾选」· 「移出选中」
4. **Quarantine confirm** — modal/dialog stating count and that rows go to `mail_credentials.dead.txt`
5. **Feedback** — existing `showOpsFeedback` / toast / OpsFeedbackBar

Keep existing mail config + import UI on the same tab; probe is an additional section, not a replacement.

---

## 8. Concurrency, safety, performance

- Pool size ~64k: **always** sample with `limit`; never full-file OAuth in one API call
- Probe workers: ThreadPoolExecutor, hard cap **8**
- File writes: single-writer; lock around quarantine rewrite; backup first
- Do not log or return secrets
- Prefer not importing full `grok_register_ttk` GUI stack into FastAPI; isolate OAuth refresh
- After quarantine, live mtime changes — next register load picks up new pool

---

## 9. Testing

| Layer | What |
|-------|------|
| Unit core | domain filter; sample with seed; status classification from sample error strings; quarantine rewrite on temp dir (backup + dead append + live remainder) |
| Unit API | schema validation (limit max, empty emails rejected); handlers with mocked core |
| UI | manual / light: probe button calls API; dead rows selectable; quarantine confirm |
| Live | optional; not required in CI (`GROK_REGISTER_LIVE` style only if already used elsewhere) |

Mock the refresh callable in unit tests — no network.

---

## 10. Integration points (existing code)

- Path resolution: same rules as `get_hotmail_accounts_file` / import_mail
- Auth: `apps/control_api/auth.py` `require_auth`
- Router mount: `apps/control_api/app.py` like accounts/ops
- Import pattern reference: `imports_ops.import_mail` (backup + write)
- Probe pattern reference: `nodes_ops` ThreadPoolExecutor
- UI pattern reference: `AccountsPage` table/delete; `NodesTab` test/prune; `MailTab` host page

---

## 11. Success criteria

1. Operator can open 资源 → 邮箱, select Hotmail and/or Outlook, probe N accounts, see 好用 vs 挂了 with reasons.
2. Operator can select dead rows and quarantine them; main pool shrinks; dead file grows; backup exists.
3. CLI can run the same probe offline for ops/SSH.
4. No credential secrets appear in API JSON or browser UI.
5. Unit tests cover filter/classify/quarantine without live Microsoft calls.

---

## 12. Non-goals reminder

- Not a replacement for Cloudflare / other `email_provider` paths
- Not a guarantee of OTP delivery (only OAuth aliveness)
- Not a full-pool health dashboard with historical charts
