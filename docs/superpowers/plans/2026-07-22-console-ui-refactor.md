# Console UI Refactor (console10) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `apps/web` as a Preact + Vite SPA with 5-page IA, design tokens, modular API client, and responsive shell — zero backend contract changes.

**Architecture:** Scaffold Vite under `apps/web`, port each page into Preact components driven by `@preact/signals`, keep cookie/Bearer auth and all `/api/*` paths identical. Build outputs to `apps/web/dist`; FastAPI mounts `dist` (legacy console9 stays until parity). Deploy builds locally then scp `dist` to pxed (model B).

**Tech Stack:** Preact 10 + `@preact/signals`, Vite 6, CSS custom properties (no Tailwind), FastAPI StaticFiles, Python backend untouched.

## Global Constraints

- **Backend zero-change:** no edits to `/api/*` contracts, auth, runs, nodes, accounts, import, ops.
- **Product contract:** disk-first; supervisor hard-forces `CPA_PROBE_CHAT=false` / mid-bulk inject off; UI must still send those knobs as today.
- **Auth:** cookie session + optional Bearer in `sessionStorage` (`controlToken`); login gate; 401 → show gate.
- **GitHub banner:** keep `https://github.com/dengyie/ai-register-machine`.
- **Deploy model B:** in-repo; build locally; scp `dist`; do not disrupt live batch on pxed.
- **Poll interval:** keep ~4s for run status; `regFormDirty` equivalent must not wipe form edits on poll.
- **Ops feedback:** never silent top-bar actions — toast + sticky banner + ops log.
- **Stop UX:** confirm before stop (kills lock-held external supervisors); no chromium pkill UI.
- **Copy language:** Chinese UI labels as today.
- **Repo path:** `/Users/mango/project/claude-project/grok-register`
- **Spec:** `docs/superpowers/specs/2026-07-22-console-ui-refactor-design.md`
- **Baseline:** console9 `apps/web/index.html` + `assets/app.js` + `assets/app.css`; FastAPI mount `apps/control_api/app.py:98-100`

## File map

| Path | Role |
|------|------|
| `apps/web/package.json` | preact, @preact/signals, vite, @preact/preset-vite |
| `apps/web/vite.config.js` | base `/`, outDir `dist`, emptyOutDir true, dev proxy `/api` → `http://127.0.0.1:8787` |
| `apps/web/index.html` | Vite entry (replaces legacy root after cutover) |
| `apps/web/src/main.jsx` | render `<App />` into `#root` |
| `apps/web/src/App.jsx` | auth gate + shell + hash router |
| `apps/web/src/api/client.js` | `api()`, `headers()`, multipart, all endpoint helpers |
| `apps/web/src/store/*.js` | signals: session, run, feedback, config, accounts, nodes |
| `apps/web/src/lib/format.js` | `pct`, `fmtNum`, `dash`, `escapeHtml`, `healthBadge`, `formatApiError` |
| `apps/web/src/ui/*` | Button, Card, Field, Select, Table, ToastHost, StatusDot, Chip, Kpi, Bar, Tabs, Drawer |
| `apps/web/src/styles/tokens.css` | design tokens |
| `apps/web/src/styles/base.css` | reset + shell layout + responsive |
| `apps/web/src/styles/components.css` | primitive component styles |
| `apps/web/src/pages/Register/*` | form + RunProgress pipeline |
| `apps/web/src/pages/Logs/*` | tail + history |
| `apps/web/src/pages/Accounts/*` | table + filters |
| `apps/web/src/pages/Resources/*` | tabs: Nodes, Mail, Import |
| `apps/web/src/pages/Settings/*` | secrets + bearer + selfcheck/cleanup only |
| `apps/control_api/app.py` | mount prefers `web/dist` then falls back to `web` |
| `scripts/build_web_console.sh` | `npm ci && npm run build` in `apps/web` |
| `scripts/deploy_web_console10.sh` | build + scp `dist` to pxed (manual) |
| `apps/web/legacy/` | move console9 files here during cutover so rollback is one path flip |
| `.gitignore` | ensure `apps/web/node_modules/` ignored (root already has `dist/`) |

**Does not touch:** progress.py, runs semantics, coinbot, clash import backend, auth backend.

---

### Task 1: Scaffold Vite + Preact under `apps/web`

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/vite.config.js`
- Create: `apps/web/src/main.jsx`
- Create: `apps/web/src/App.jsx` (hello shell)
- Create: `apps/web/src/styles/tokens.css`, `base.css`
- Modify: `.gitignore` — add `apps/web/node_modules/` if missing
- Keep: existing `apps/web/index.html` + `assets/*` until Task 9 cutover (scaffold uses a temporary entry or coexists carefully — see steps)

**Interfaces:**
- Consumes: none
- Produces: `npm run dev` / `npm run build` work; `dist/index.html` + assets

- [ ] **Step 1: Create package.json**

```json
{
  "name": "ai-register-console",
  "private": true,
  "version": "10.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "preact": "^10.26.4",
    "@preact/signals": "^2.0.4"
  },
  "devDependencies": {
    "@preact/preset-vite": "^2.10.1",
    "vite": "^6.2.0"
  }
}
```

- [ ] **Step 2: Create vite.config.js**

```js
import { defineConfig } from "vite";
import preact from "@preact/preset-vite";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [preact()],
  base: "/",
  root: resolve(__dirname),
  publicDir: false,
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8787",
        changeOrigin: true,
      },
    },
  },
});
```

- [ ] **Step 3: Move legacy console9 aside and add Vite entry**

```bash
mkdir -p apps/web/legacy
mv apps/web/index.html apps/web/legacy/index.html
mv apps/web/assets apps/web/legacy/assets
```

Create new `apps/web/index.html`:

```html
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>AI 注册机 · Control Plane</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

> Rollback path: restore `legacy/` to root and revert FastAPI mount.

- [ ] **Step 4: Hello App**

`src/main.jsx`:

```jsx
import { render } from "preact";
import { App } from "./App.jsx";
import "./styles/tokens.css";
import "./styles/base.css";

render(<App />, document.getElementById("root"));
```

`src/App.jsx`:

```jsx
export function App() {
  return (
    <div class="app-shell-stub">
      <h1>AI 注册机 · console10 scaffold</h1>
      <p class="hint">Vite + Preact OK</p>
    </div>
  );
}
```

`src/styles/tokens.css` — port color roles from legacy `app.css:1-17`:

```css
:root {
  --bg: #0b0f14;
  --panel: #121a24;
  --panel-2: #182231;
  --text: #e8eef7;
  --muted: #8b9bb4;
  --accent: #5b9fd4;
  --accent-2: #3d7eb0;
  --danger: #d45b5b;
  --ok: #5bd49a;
  --warn: #d4b15b;
  --border: #243044;
  --sidebar: #0a1018;
  --sidebar-w: 220px;
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 24px;
  --space-6: 32px;
  --font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --shadow-card: 0 18px 50px rgba(0, 0, 0, 0.35);
}
```

`src/styles/base.css` minimal:

```css
* { box-sizing: border-box; }
html, body { margin: 0; min-height: 100%; background: var(--bg); color: var(--text); font-family: var(--font); }
.hidden { display: none !important; }
.hint { color: var(--muted); font-size: 0.9rem; }
```

- [ ] **Step 5: Install + verify**

```bash
cd apps/web && npm install
npm run build
ls dist/index.html dist/assets/
```

Expected: build succeeds; `dist/` contains hashed JS/CSS.

```bash
npm run dev
# open http://127.0.0.1:5173 — stub page renders
```

- [ ] **Step 6: Commit**

```bash
git add apps/web/package.json apps/web/package-lock.json apps/web/vite.config.js \
  apps/web/index.html apps/web/src apps/web/legacy .gitignore
git commit -m "feat(web): scaffold console10 Vite+Preact (legacy → apps/web/legacy)"
```

---

### Task 2: API client + format helpers

**Files:**
- Create: `apps/web/src/api/client.js`
- Create: `apps/web/src/lib/format.js`
- Create: `apps/web/src/store/session.js`
- Test manually against running control_api (or unit-test helpers with node)

**Interfaces:**
- Consumes: `/api/*` as listed in spec §5
- Produces:
  - `api(path, opts)` → body or throws `Error` with `.status`
  - `headers(json?: boolean)`
  - `getToken()` / `setToken(t)` / `clearToken()`
  - named helpers: `login`, `logout`, `me`, `overview`, `getConfig`, `putConfig`, `startRun`, `stopRun`, `currentRun`, `listRuns`, `runLogs`, `listAccounts`, `listClashNodes`, `importClashUrl`, `testClash`, `listCatalogNodes`, `testCatalog`, `importNodes`, `importMail`, `importAuths`, `importPack`, `selfcheck`, `cleanupOrphans`
  - format: `pct(a,b)`, `fmtNum(v)`, `dash(v)`, `escapeHtml(s)`, `formatApiError(e)`, `healthBadge(h)`

- [ ] **Step 1: Write format helpers** (`src/lib/format.js`)

```js
export function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function dash(v) {
  return v == null || v === "" ? "—" : String(v);
}

export function fmtNum(v) {
  return v == null || v === "" ? "—" : String(v);
}

export function pct(a, b) {
  if (b == null || a == null || Number(b) <= 0) return null;
  return Math.max(0, Math.min(100, (Number(a) / Number(b)) * 100));
}

export function formatApiError(e) {
  if (!e) return "unknown error";
  if (typeof e === "string") return e;
  if (e.message) return e.status ? `${e.status}: ${e.message}` : e.message;
  return String(e);
}

export function healthBadge(h) {
  if (h === "ok" || h === true) return { label: "ok", cls: "ok" };
  if (h === "fail" || h === false) return { label: "fail", cls: "danger" };
  return { label: "?", cls: "muted" };
}
```

- [ ] **Step 2: Write API client** (`src/api/client.js`)

Port logic from legacy `app.js` `token`/`headers`/`api`/`postMultipart` exactly:

```js
const TOKEN_KEY = "controlToken";

export function getToken() {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}
export function setToken(t) {
  if (t) sessionStorage.setItem(TOKEN_KEY, t);
  else sessionStorage.removeItem(TOKEN_KEY);
}
export function clearToken() {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function headers(json = false) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  const t = getToken();
  if (t) h["Authorization"] = `Bearer ${t}`;
  return h;
}

export async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts });
  const text = await res.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { detail: text };
  }
  if (!res.ok) {
    const detail = body.detail || res.statusText;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = res.status;
    throw err;
  }
  return body;
}

export async function postMultipart(url, formData) {
  const res = await fetch(url, {
    method: "POST",
    headers: headers(),
    body: formData,
    credentials: "same-origin",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.detail || res.statusText);
    err.status = res.status;
    throw err;
  }
  return body;
}

// Auth
export const login = (username, password) =>
  api("/api/auth/login", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ username, password }),
  });
export const logout = () => api("/api/auth/logout", { method: "POST", headers: headers(true) });
export const me = () => api("/api/auth/me");

// Core
export const overview = () => api("/api/overview");
export const getConfig = () => api("/api/config");
export const putConfig = (partial) =>
  api("/api/config", {
    method: "PUT",
    headers: headers(true),
    body: JSON.stringify(partial),
  });

// Runs
export const listRuns = () => api("/api/runs");
export const currentRun = () => api("/api/runs/current");
export const startRun = (body) =>
  api("/api/runs/start", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
export const stopRun = () =>
  api("/api/runs/stop", { method: "POST", headers: headers(true) });
export const runLogs = (tail = 200, which = "auto") =>
  api(`/api/runs/current/logs?tail=${tail}&which=${which}`);

// Accounts
export const listAccounts = (qs) => api(`/api/accounts?${qs}`);
export const accountAction = (id, action) =>
  api(`/api/accounts/${encodeURIComponent(id)}`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ action }),
  });

// Nodes
export const listClash = () => api("/api/nodes/clash");
export const testClash = (body) =>
  api("/api/nodes/clash/test", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body || {}),
  });
export const importClashUrl = (body) =>
  api("/api/nodes/clash/import-url", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
export const listCatalog = (qs) => api(`/api/nodes?${qs}`);
export const addCatalogNode = (body) =>
  api("/api/nodes", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
export const testCatalog = (body) =>
  api("/api/nodes/test", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body || {}),
  });

// Import multipart
export const importNodesFile = (fd) => postMultipart("/api/import/nodes", fd);
export const importMailText = (fd) => postMultipart("/api/import/mail", fd);
export const importAuthsFile = (fd) => postMultipart("/api/import/auths", fd);
export const importPackFile = (fd) => postMultipart("/api/import/pack", fd);

// Ops
export const selfcheck = () =>
  api("/api/ops/selfcheck", { method: "POST", headers: headers(true) });
export const cleanupOrphans = () =>
  api("/api/ops/cleanup-orphans?dry_run=false", {
    method: "POST",
    headers: headers(true),
  });
```

> If any account/node action signature differs when porting a page, adjust the helper to match the **live** call site in `legacy/assets/app.js` — do not invent new request shapes.

- [ ] **Step 3: Session signal store**

```js
// src/store/session.js
import { signal } from "@preact/signals";

export const session = signal({
  authenticated: false,
  username: null,
  auth_required: true,
  password_login_enabled: true,
  users_configured: false,
  checked: false,
});
```

- [ ] **Step 4: Smoke test client against control_api**

```bash
# terminal A
./scripts/run_control_api.sh
# terminal B
cd apps/web && npm run dev
# browser console or temporary button:
# await fetch('/api/auth/me', {credentials:'same-origin'}).then(r=>r.json())
```

Expected: proxy returns 200 JSON from control_api.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/api apps/web/src/lib apps/web/src/store
git commit -m "feat(web): api client + format helpers + session signal"
```

---

### Task 3: UI primitives + feedback system + shell layout

**Files:**
- Create: `apps/web/src/ui/Button.jsx`, `Card.jsx`, `Field.jsx`, `Select.jsx`, `Tabs.jsx`, `StatusDot.jsx`, `Chip.jsx`, `Kpi.jsx`, `Bar.jsx`, `ToastHost.jsx`, `Drawer.jsx`, `index.js`
- Create: `apps/web/src/styles/components.css`
- Create: `apps/web/src/store/feedback.js`
- Modify: `App.jsx` — login gate + sidebar shell + hash router stubs
- Modify: `base.css` — shell grid + responsive sidebar

**Interfaces:**
- Produces:
  - `<Button variant="primary|ghost|danger" size="sm|md" busy={bool}>`
  - `<Card>`, `<Field label=...>`, `<Tabs items value onChange>`
  - `showOpsFeedback(message, kind, opts)` from feedback store
  - Hash routes: `#/register` `#/logs` `#/accounts` `#/resources` `#/settings` (default register)

- [ ] **Step 1: Feedback store** (port ops toast/log from legacy)

```js
// src/store/feedback.js
import { signal } from "@preact/signals";

const OPS_LOG_MAX = 40;
export const opsLog = signal([]);
export const stickyBanner = signal(null); // { message, kind } | null
export const toasts = signal([]); // { id, message, kind }[]

let toastSeq = 0;

export function pushOpsLog(message, kind = "info") {
  const t = new Date().toTimeString().slice(0, 8);
  const next = [{ t, kind, m: String(message || "") }, ...opsLog.value];
  if (next.length > OPS_LOG_MAX) next.length = OPS_LOG_MAX;
  opsLog.value = next;
}

export function showOpsFeedback(
  message,
  kind = "info",
  { toast = true, sticky = true, log = true } = {},
) {
  const text = String(message || "").trim() || "(无消息)";
  if (sticky) stickyBanner.value = { message: text, kind };
  if (log) pushOpsLog(text, kind);
  if (!toast) return;
  const id = ++toastSeq;
  toasts.value = [...toasts.value, { id, message: text.length > 220 ? text.slice(0, 217) + "…" : text, kind }];
  const ms = kind === "err" ? 6500 : 3200;
  setTimeout(() => {
    toasts.value = toasts.value.filter((x) => x.id !== id);
  }, ms);
}
```

- [ ] **Step 2: Minimal primitives**

```jsx
// src/ui/Button.jsx
export function Button({
  variant = "ghost",
  size = "md",
  busy = false,
  type = "button",
  class: cls = "",
  children,
  ...rest
}) {
  return (
    <button
      type={type}
      class={`btn btn-${variant} btn-${size} ${busy ? "busy" : ""} ${cls}`}
      disabled={busy || rest.disabled}
      {...rest}
    >
      {busy ? "…" : children}
    </button>
  );
}
```

```jsx
// src/ui/Card.jsx
export function Card({ children, class: cls = "", ...rest }) {
  return (
    <div class={`card ${cls}`} {...rest}>
      {children}
    </div>
  );
}
```

```jsx
// src/ui/Field.jsx
export function Field({ label, span2 = false, children, class: cls = "" }) {
  return (
    <label class={`field ${span2 ? "span2" : ""} ${cls}`}>
      {label ? <span class="field-label">{label}</span> : null}
      {children}
    </label>
  );
}
```

```jsx
// src/ui/Tabs.jsx
export function Tabs({ items, value, onChange }) {
  // items: [{ id, label }]
  return (
    <div class="tabrow" role="tablist">
      {items.map((it) => (
        <button
          type="button"
          role="tab"
          class={`tab ${value === it.id ? "active" : ""}`}
          aria-selected={value === it.id}
          onClick={() => onChange(it.id)}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
```

```jsx
// src/ui/ToastHost.jsx
import { toasts } from "../store/feedback.js";

export function ToastHost() {
  return (
    <div class="ops-toast-host" aria-live="polite">
      {toasts.value.map((t) => (
        <div key={t.id} class={`ops-toast ${t.kind}`}>
          {t.message}
        </div>
      ))}
    </div>
  );
}
```

Port remaining StatusDot / Chip / Kpi / Bar styles into `components.css` matching legacy classes (`.kpi`, `.bar-row`, `.status-dot`, `.chip`).

- [ ] **Step 3: Hash router + shell in App.jsx**

```jsx
import { useEffect, useState } from "preact/hooks";
import { session } from "./store/session.js";
import * as api from "./api/client.js";
import { ToastHost } from "./ui/ToastHost.jsx";
import { LoginGate } from "./pages/LoginGate.jsx";
// page stubs for now
import { RegisterPage } from "./pages/Register/RegisterPage.jsx";
import { LogsPage } from "./pages/Logs/LogsPage.jsx";
import { AccountsPage } from "./pages/Accounts/AccountsPage.jsx";
import { ResourcesPage } from "./pages/Resources/ResourcesPage.jsx";
import { SettingsPage } from "./pages/Settings/SettingsPage.jsx";

const NAV = [
  { id: "register", label: "总览 / 注册", hash: "#/register" },
  { id: "logs", label: "运行日志", hash: "#/logs" },
  { id: "accounts", label: "账号池", hash: "#/accounts" },
  { id: "resources", label: "资源", hash: "#/resources" },
  { id: "settings", label: "设置", hash: "#/settings" },
];

function pageFromHash() {
  const h = (location.hash || "#/register").replace(/^#\/?/, "");
  const id = h.split("?")[0] || "register";
  return NAV.some((n) => n.id === id) ? id : "register";
}

export function App() {
  const [page, setPage] = useState(pageFromHash);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const m = await api.me();
        session.value = { ...session.value, ...m, checked: true };
      } catch {
        session.value = { ...session.value, authenticated: false, checked: true };
      }
    })();
  }, []);

  const authed = session.value.authenticated || !session.value.auth_required;

  if (!session.value.checked) return <div class="login-gate">加载中…</div>;
  if (!authed) return <LoginGate />;

  const Page = {
    register: RegisterPage,
    logs: LogsPage,
    accounts: AccountsPage,
    resources: ResourcesPage,
    settings: SettingsPage,
  }[page];

  return (
    <>
      <ToastHost />
      <div class={`app-shell ${sidebarOpen ? "nav-open" : ""}`}>
        <button type="button" class="nav-toggle" onClick={() => setSidebarOpen((v) => !v)}>
          菜单
        </button>
        <aside class="sidebar">
          <div class="side-brand">
            <div class="logo-dot" aria-hidden="true" />
            <div>
              <div class="side-title">AI 注册机</div>
              <a
                class="side-link"
                href="https://github.com/dengyie/ai-register-machine"
                target="_blank"
                rel="noopener noreferrer"
              >
                grok / mimo / chatgpt
              </a>
            </div>
          </div>
          <nav class="side-nav">
            {NAV.map((n) => (
              <a
                key={n.id}
                href={n.hash}
                class={`nav-item ${page === n.id ? "active" : ""}`}
                onClick={() => setSidebarOpen(false)}
              >
                <span class="nav-dot" />
                {n.label}
              </a>
            ))}
          </nav>
          <div class="side-foot">
            <div class="hint">{session.value.username || "operator"}</div>
            <button
              type="button"
              class="btn btn-danger btn-sm"
              onClick={async () => {
                try {
                  await api.logout();
                } finally {
                  api.clearToken();
                  session.value = { ...session.value, authenticated: false, username: null };
                }
              }}
            >
              Logout
            </button>
          </div>
        </aside>
        <div class="main-wrap">
          <Page />
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 4: LoginGate + page stubs**

`LoginGate.jsx` — port legacy login form fields/labels.

Each page stub:

```jsx
export function LogsPage() {
  return (
    <section class="page">
      <header class="page-head"><h1>运行日志</h1></header>
      <p class="hint">stub — Task 5+</p>
    </section>
  );
}
```

Same for Accounts / Resources / Settings / Register.

- [ ] **Step 5: Responsive shell CSS**

In `base.css`:

```css
.app-shell {
  display: grid;
  grid-template-columns: var(--sidebar-w) 1fr;
  min-height: 100vh;
}
.sidebar {
  background: var(--sidebar);
  border-right: 1px solid var(--border);
  position: sticky;
  top: 0;
  height: 100vh;
  display: flex;
  flex-direction: column;
  padding: var(--space-4) var(--space-3);
}
.nav-toggle { display: none; }
@media (max-width: 900px) {
  .app-shell { grid-template-columns: 1fr; }
  .nav-toggle {
    display: inline-flex;
    position: fixed;
    top: var(--space-3);
    left: var(--space-3);
    z-index: 30;
  }
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    width: min(280px, 86vw);
    transform: translateX(-105%);
    transition: transform 0.18s ease;
    z-index: 40;
  }
  .app-shell.nav-open .sidebar { transform: translateX(0); }
}
```

- [ ] **Step 6: Verify**

```bash
cd apps/web && npm run build
npm run dev
```

Expected: login gate or shell with 5 nav items; hash navigation swaps stub pages; mobile width collapses sidebar.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src
git commit -m "feat(web): shell, hash router, UI primitives, ops feedback store"
```

---

### Task 4: Register page + RunProgress (highest priority)

**Files:**
- Create: `apps/web/src/pages/Register/RegisterPage.jsx`
- Create: `apps/web/src/pages/Register/RegForm.jsx`
- Create: `apps/web/src/pages/Register/RunProgress.jsx`
- Create: `apps/web/src/pages/Register/progressRender.jsx` (port kpi/bars/steps/timeline pure render)
- Create: `apps/web/src/store/run.js`
- Create: `apps/web/src/styles/run.css`

**Interfaces:**
- Consumes: `api.startRun`, `stopRun`, `currentRun`, `overview`, `getConfig`, `putConfig`, `selfcheck` (link-out only), feedback store
- Produces: full register UX parity with legacy start body construction (kind/product/extra_env keys identical to `legacy/assets/app.js` startRun)

- [ ] **Step 1: run store + 4s poll**

```js
// src/store/run.js
import { signal } from "@preact/signals";
export const currentRunState = signal(null); // run object
export const overviewState = signal(null);
export const regFormDirty = signal(false);
```

In `RegisterPage`, `useEffect` poll every 4000ms while page mounted:

```js
async function tick() {
  try {
    const [run, ov] = await Promise.all([api.currentRun(), api.overview()]);
    currentRunState.value = run?.run ?? run;
    overviewState.value = ov;
  } catch (e) {
    if (e.status === 401) {
      session.value = { ...session.value, authenticated: false };
    }
  }
}
```

Do **not** reload form fields from config while `regFormDirty.value === true`.

- [ ] **Step 2: Port start body exactly**

Mirror legacy:

```js
const body = {
  kind, // grok_supervisor | register_sh
  product, // grok|mimo|chatgpt (supervisor → force grok)
  mode, // ordinary|residential
  target: Number(target),
  threads: Number(threads),
  tag: tag.trim() || "batch_web",
  extra_env: {
    // SUPERVISOR_CHUNK, CPA_BATCH_END_INJECT, CPA_BATCH_IMPORT_EVERY/SIZE/PAUSE
    // CPA_PROBE_CHAT: "false"
    // SKIP_CLASH_PREFLIGHT, NODE_SCORE
    // EMAIL_PROVIDER, DEFAULT_DOMAINS when sync checked
  },
};
```

Stop: `window.confirm` Chinese warning, then `api.stopRun()`.

- [ ] **Step 3: RunProgress components**

Port pure HTML builders from legacy `renderRunHeader` / `renderKpiGrid` / `renderBars` / `renderStepRail` / `renderStatusCard` / `renderRecentWrites` / `renderTimeline` into JSX components. Visual upgrade:

- Hero header: larger status word + StatusDot + chips
- KPI grid uses `<Kpi />`
- Step rail uses done/active/pending classes
- Timeline collapsible

- [ ] **Step 4: Form layout**

Left panel:
- Common fields always visible: email_provider, mail key, domains, target, threads, mode, tag
- Single advanced `<Drawer>` / `<details>` containing batch opts + proxy opts + kind/product/SKIP_CLASH/NODE_SCORE (merge four legacy details into one)
- Footer toolbar: 保存 / 自检(link `#/settings`) / 测代理
- Remove duplicate cleanup/selfcheck buttons (settings only)

Right panel: RunProgress + link to logs.

Top: sticky ops banner + ops log collapsible (from feedback store).

- [ ] **Step 5: Parity checklist (hand)**

With control_api running:

1. Load form from `/api/config`
2. Edit target → poll does not wipe
3. 保存 → putConfig
4. 开始 → startRun toast ok / 409 refresh
5. 停止 → confirm → stopped
6. KPI/bars/steps update while alive
7. stuck state shows danger

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/pages/Register apps/web/src/store/run.js apps/web/src/styles/run.css
git commit -m "feat(web): Register page + RunProgress pipeline (console10)"
```

---

### Task 5: Logs page

**Files:**
- Create: `apps/web/src/pages/Logs/LogsPage.jsx`

**Interfaces:**
- Consumes: `api.runLogs`, `currentRun`, `listRuns`
- Produces: which/tail/follow, path summary, history list

- [ ] **Step 1: Port logs UI** from legacy `#page-logs` — status bar, path line, log toolbar, `<pre class="log mono">`, history details.

- [ ] **Step 2: Follow timer** — when checkbox on and page mounted, poll logs every 4s; clear on unmount.

- [ ] **Step 3: Hand-check** which=worker/supervisor/both, tail sizes, history renders.

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(web): Logs page with follow tail + history"
```

---

### Task 6: Accounts page

**Files:**
- Create: `apps/web/src/pages/Accounts/AccountsPage.jsx`

**Interfaces:**
- Consumes: `api.listAccounts`, row actions as in legacy
- Produces: filter bar, pagination, summary KPIs, data table

- [ ] **Step 1: Port query builder** (`q`, complete, page, page_size) from legacy `accountsQuery()`.

- [ ] **Step 2: Table + actions** — complete/incomplete badge, mtime, priority, kind, per-row buttons matching legacy handlers.

- [ ] **Step 3: Hand-check** search, complete filter, page next/prev.

- [ ] **Step 4: Commit**

```bash
git commit -am "feat(web): Accounts page"
```

---

### Task 7: Resources page (Nodes + Mail + Import tabs)

**Files:**
- Create: `apps/web/src/pages/Resources/ResourcesPage.jsx`
- Create: `apps/web/src/pages/Resources/NodesTab.jsx` (Clash + catalog subtabs)
- Create: `apps/web/src/pages/Resources/MailTab.jsx`
- Create: `apps/web/src/pages/Resources/ImportTab.jsx`

**Interfaces:**
- Consumes: all nodes/mail/import endpoints
- Produces: IA merge per spec §6 — no duplicate mail cred import elsewhere

- [ ] **Step 1: Resources shell with Tabs** `nodes | mail | import` (URL hash optional: `#/resources?tab=nodes`).

- [ ] **Step 2: NodesTab** — port Clash leaf table, groups chips, subscription import form (dry-run + timings toast from `data.timings`/`total_ms`), catalog table + add form. Subtabs Clash | catalog.

- [ ] **Step 3: MailTab** — port mail form + hotmail cred import (the single remaining mail-cred surface).

- [ ] **Step 4: ImportTab** — 2×2 cards: nodes file, mail text, auths file, pack zip — same FormData fields as legacy.

- [ ] **Step 5: Hand-check** clash refresh, dry-run import toast ms, mail save, each import card.

- [ ] **Step 6: Commit**

```bash
git commit -am "feat(web): Resources page (nodes/mail/import tabs)"
```

---

### Task 8: Settings page (deduped)

**Files:**
- Create: `apps/web/src/pages/Settings/SettingsPage.jsx`

**Interfaces:**
- Consumes: `getConfig`/`putConfig`, bearer token local, `selfcheck`, `cleanupOrphans`
- Produces: **only** global secrets + bearer + 运维自检/清理 (no full email form duplicate)

- [ ] **Step 1: Form fields** — keep global: proxy, proxy_rotate_mode, proxy_list, turnstile_stuck_timeout, cpa_probe_chat, cpa_remote_inject intent, secret keys (masked leave-empty). Drop fields owned exclusively by MailTab (email_provider UI, hotmail file, plus alias) if they only lived on mail page — keep putConfig keys that settings still edits today carefully: if unsure, compare legacy settings form field list and mail form field list; intersection of pure-mail fields stays MailTab-only.

- [ ] **Step 2: Bearer token** — read/write `sessionStorage` via `setToken`.

- [ ] **Step 3: Ops** — 运行自检 / 清理过盾残留 with feedback toasts (single copy).

- [ ] **Step 4: Hand-check** save secrets leave-empty keep, selfcheck, cleanup confirm if any.

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(web): Settings page (deduped ops + secrets)"
```

---

### Task 9: FastAPI mount + build/deploy scripts + cutover

**Files:**
- Modify: `apps/control_api/app.py` (mount logic)
- Create: `scripts/build_web_console.sh`
- Create: `scripts/deploy_web_console10.sh`
- Modify: `apps/README.md` (build note one paragraph)
- Optionally delete or keep `apps/web/legacy/` until post-deploy confidence

**Interfaces:**
- Produces: production serves `apps/web/dist`; local without dist still can serve legacy if present

- [ ] **Step 1: Mount prefers dist**

Replace `app.py` web mount block:

```python
    web_root = Path(__file__).resolve().parents[1] / "web"
    web_dist = web_root / "dist"
    web_dir = web_dist if (web_dist / "index.html").is_file() else web_root
    # Prefer Vite build; fall back to flat legacy/static root if no dist yet.
    if web_dir.is_dir() and (web_dir / "index.html").is_file():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
```

- [ ] **Step 2: build script**

```bash
#!/usr/bin/env bash
# scripts/build_web_console.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/apps/web"
if [[ ! -d node_modules ]]; then
  npm ci || npm install
fi
npm run build
echo "[web] built → $ROOT/apps/web/dist"
```

```bash
chmod +x scripts/build_web_console.sh
```

- [ ] **Step 3: deploy script (local build + scp)**

```bash
#!/usr/bin/env bash
# scripts/deploy_web_console10.sh — MANUAL only; does not stop batch/coinbot
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOST="${PXED_HOST:-pxed}"
REMOTE="${PXED_WEB:-/data/grok-register/apps/web}"
"$ROOT/scripts/build_web_console.sh"
# ship dist only
tar -C "$ROOT/apps/web" -czf /tmp/console10-dist.tgz dist
scp /tmp/console10-dist.tgz "$HOST:/tmp/"
ssh "$HOST" "mkdir -p '$REMOTE' && tar -C '$REMOTE' -xzf /tmp/console10-dist.tgz && ls -la '$REMOTE/dist' | head"
echo "[deploy] dist on $HOST:$REMOTE/dist — restart control_api if needed (static only usually hot)"
```

Adjust `PXED_*` paths to match real layout (`/personal/grok-register` or `/data/grok-register`) before first run.

- [ ] **Step 4: Backend tests still green**

```bash
uv run pytest tests/unit/test_control_api_login.py -q
```

Expected: PASS (no API change).

- [ ] **Step 5: Full local serve check**

```bash
./scripts/build_web_console.sh
./scripts/run_control_api.sh
# open http://127.0.0.1:8787 — console10 from dist
```

Parity smoke: login → register start/stop → logs follow → accounts page → resources clash list → settings selfcheck.

- [ ] **Step 6: Commit**

```bash
git add apps/control_api/app.py scripts/build_web_console.sh scripts/deploy_web_console10.sh apps/README.md
git commit -m "feat(web): serve dist build; build/deploy scripts for console10"
```

---

### Task 10: Visual polish + responsive pass + remove legacy

**Files:**
- Modify: `src/styles/*` — tighten tokens, run hero, tables
- Delete (after pxed confidence): `apps/web/legacy/` optional later commit
- Modify: README control-plane blurb if still says “static only no build”

- [ ] **Step 1: Visual pass checklist**
  - buttons consistent primary/ghost/danger
  - tables: sticky header, row hover, mono cells
  - run hero scannable at a glance
  - toasts top-right, never clip on mobile

- [ ] **Step 2: Responsive checklist**
  - 375 / 768 / 1280 widths
  - sidebar drawer
  - register split stacks vertically <900px
  - tables horizontal scroll

- [ ] **Step 3: Optional remove legacy** only after live deploy OK:

```bash
rm -rf apps/web/legacy
git commit -am "chore(web): drop console9 legacy after console10 cutover"
```

- [ ] **Step 4: Final commit for polish**

```bash
git commit -am "style(web): console10 visual + responsive polish"
```

---

## Parity matrix (all pages)

| Area | Legacy source | console10 location | Must work |
|------|---------------|--------------------|-----------|
| Login | index + doLogin | LoginGate | cookie session |
| Start/stop | startRun/stopRun | RegisterPage | body keys identical |
| Progress | render* | RunProgress | KPI/bars/steps/timeline/writes |
| Logs | page-logs | LogsPage | which/tail/follow/history |
| Accounts | page-accounts | AccountsPage | filter/page/actions |
| Clash nodes | page-nodes clash | Resources/NodesTab | list/test/import-url |
| Catalog | page-nodes catalog | Resources/NodesTab | list/add/test |
| Mail config | page-mail | Resources/MailTab | save + hotmail import |
| Import 4 cards | page-import | Resources/ImportTab | multipart fields |
| Settings secrets | page-settings | SettingsPage | leave-empty keep |
| Selfcheck/cleanup | settings only after refactor | SettingsPage | single copy |
| Ops toast | showOpsFeedback | feedback store | never silent |

---

## Spec coverage self-review

| Spec section | Task(s) |
|--------------|---------|
| §2 Goals (IA/tokens/Preact/responsive/monitor) | 1,3,4,7,10 |
| §3 Non-goals (no API change) | Global + Task 9 tests |
| §4 Locked decisions | 1–3 |
| §5 API contract | Task 2 |
| §6 IA 5 pages | 3–8 |
| §7 Code architecture | File map + Tasks 1–8 |
| §8 Visual system | 3,4,10 |
| §9 Migration | 1 (legacy move), 9 cutover |
| §10 Testing | per-task hand-check + Task 9 pytest |
| §11 Risks (deploy/parity/bundle) | 9,10 + deploy script |
| Open Q deploy: local build + scp | Task 9 script |
| Open Q hash router | Task 3 |

## Placeholder scan

No TBD/TODO steps; all endpoint paths and startRun body keys spelled out; signatures match legacy client.

## Type consistency

- `api(path, opts)` / `headers(json)` used throughout
- `showOpsFeedback(message, kind, opts)` single signature
- Hash ids: `register|logs|accounts|resources|settings`
- Stores: `session`, `currentRunState`, `overviewState`, `regFormDirty`, `opsLog`, `toasts`, `stickyBanner`

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-22-console-ui-refactor.md`.

**Two execution options:**

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration  
2. **Inline Execution** — execute tasks in this session with executing-plans and checkpoints  

Which approach?
