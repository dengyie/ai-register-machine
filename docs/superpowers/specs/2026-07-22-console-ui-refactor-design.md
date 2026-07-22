# Console UI Refactor Design (console10)

**Date:** 2026-07-22
**Status:** Draft — awaiting written-spec review before implementation plan
**Product:** ai-register-machine · web control plane (`apps/web`)
**Supersedes visual/structure of:** console9 (`index.html` + `assets/app.js` + `assets/app.css`)

## 1. Problem

The control-plane console (`apps/web`, served by FastAPI `StaticFiles` mount at `app.py:99-100`) has grown into three unstructured files with real usability and maintainability debt:

- **`assets/app.js` — 1781 lines, single IIFE.** All 22 endpoints, all 7 pages, all render logic, all state in one closure. Changing one page risks the others.
- **`assets/app.css` — 943 lines, ad-hoc.** Many one-off hardcoded values; buttons/cards/tables/fields styled inconsistently.
- **`index.html` — 631 lines.** The 注册 page alone carries 4 nested `<details>` of advanced knobs + run progress + ops log — one screen of information overload.
- **Duplicated surfaces:** 运维自检/清理 exists on both 注册 and 设置; email/proxy config exists on both 邮箱接码 and 设置; mail-credential import exists on both 导入 and 邮箱接码.
- **Run-monitor readability:** the KPI/bars/step-rail/timeline pipeline is functional but visually noisy; key run state is not immediately scannable.

Operators asked for a comprehensive redesign hitting four goals: **visual refresh, IA/navigation regrouping, front-end code structure, and responsive/mobile support.**

## 2. Goals

- Regroup 7 pages → **5**, eliminating duplicated surfaces.
- Migrate to a **component-based front end** (Preact + Vite) so pages are isolated and testable.
- Formalize a **design-token-based visual system** (spacing/radius/type/color/elevation scale) and unified component styles.
- Redesign the **run monitor** into a clearly scannable status hero.
- Make the console **usable on narrow screens** (sidebar collapses, grids and tables reflow).

## 3. Non-goals

- No change to any `/api/*` endpoint contract (backend untouched). This is front-end only.
- No new features / no new pages beyond regrouping existing ones.
- No auth model change (session cookie + optional Bearer stay as-is).
- No SSR / no server framework migration. FastAPI still serves a static bundle.
- No i18n framework — copy stays Chinese as today.
- No change to run/polling semantics or supervisor behavior.

## 4. Locked decisions

| Decision | Choice |
|----------|--------|
| Framework | **Preact + `@preact/signals`** (tiny runtime, closest to current vanilla model) |
| Build | **Vite** — `outDir: apps/web/dist`, `base: '/'` |
| Dev | Vite dev server with `/api` proxied to control_api |
| Serving | FastAPI mount points at `apps/web/dist` (was raw `apps/web`) |
| IA | **5 pages** (see §6) |
| Styling | Design tokens (CSS custom properties) + component CSS layer; keep dark palette direction |
| API access | Single `src/api/client.js` wrapping all endpoints; cookie + Bearer supported as today |
| State | Signals stores per domain (session / currentRun / accounts / nodes / config) |

## 5. API contract (must stay intact)

All endpoints the console currently calls — the refactor changes none of them:

```
/api/auth/login   /api/auth/logout   /api/auth/me
/api/overview
/api/config                                   (GET/PUT)
/api/runs   /api/runs/current   /api/runs/start   /api/runs/stop
/api/runs/current/logs?tail=&which=
/api/accounts?<query>   /api/accounts/{id}
/api/nodes   /api/nodes?<catalogQuery>   /api/nodes/{id}   /api/nodes/test
/api/nodes/clash   /api/nodes/clash/test   /api/nodes/clash/import-url
/api/import/nodes   /api/import/mail   /api/import/auths   /api/import/pack
/api/ops/selfcheck   /api/ops/cleanup-orphans?dry_run=
```

## 6. Information architecture (7 → 5)

| New page | Merges | Content |
|----------|--------|---------|
| **总览 / 注册** | 注册 + overview KPIs | Top overview KPI strip · streamlined start form (common params inline, advanced params in ONE drawer) · run progress |
| **运行日志** | 日志 | Unchanged scope: tail (which/lines) · paths · recent supervisor history |
| **账号池** | 账号池 | Unchanged scope: filter bar · summary KPIs · data table · row actions |
| **资源** | 节点池 + 邮箱接码 + 导入 | Three tabs: **节点** (Clash + catalog subtabs) · **邮箱通道** (channel config + hotmail cred import) · **批量导入** (nodes / mail / auths / pack) |
| **设置** | 设置 | Global secrets + Bearer token + **the single copy** of 运维自检/清理 |

Rules:
- 运维自检/清理 lives **only** on 设置; the 注册 page links to it instead of duplicating.
- Email/proxy channel config lives **only** under 资源 › 邮箱通道; 设置 keeps only global secrets not owned by that tab.
- Mail-credential import lives **only** under 资源 › 批量导入.

## 7. Code architecture

```
apps/web/
  index.html              # Vite entry
  vite.config.js          # base '/', outDir 'dist', dev proxy /api → control_api
  package.json            # preact, @preact/signals, vite
  src/
    main.jsx              # mount <App>, auth gate bootstrap
    api/client.js         # fetch wrapper: cookie/Bearer, error normalize, ALL /api/* calls
    store/                # signals: session, currentRun, accounts, nodes, config
    lib/                  # format helpers: pct, fmtNum, escapeHtml, healthBadge, dash
    ui/                   # primitives: Button, Card, Table, Field, Select, Toast,
                          #             StatusDot, Chip, Kpi, Bar, Drawer, Tabs
    pages/
      Register/           # RegisterPage + RunProgress (KpiGrid, Bars, StepRail,
                          #                              Timeline, StatusCard)
      Logs/
      Accounts/
      Resources/          # Tabs → Nodes (Clash+Catalog) · Mail · Import
      Settings/
    App.jsx               # sidebar nav + lightweight router (hash-based)
```

- The 1781-line IIFE decomposes into `api/client.js` (endpoints in one place) + per-page components + `ui/` primitives.
- Routing: hash-based (`#/register`, `#/logs`, …) — no router dependency, survives refresh, keeps FastAPI serving a single `index.html`.
- Polling (run status, logs follow) driven by signals + `setInterval` inside page components, cleaned up on unmount.

## 8. Visual system

- **Tokens** (CSS custom properties): spacing scale (4/8/12/16/24/32), radius (sm/md/lg), font sizes, elevation/shadow levels, plus the existing color roles (`--bg/--panel/--panel-2/--text/--muted/--accent/--ok/--warn/--danger/--border`). Keep the current dark direction.
- **Component CSS layer**: one source of truth per primitive (button variants primary/ghost/danger × sizes; card; data table; form field/grid; chip; status dot; drawer; tabs; toast).
- **Run monitor redesign**: hero status card (large state word + colored dot + meta chips), KPI row, progress bars, horizontal step rail with done/active/pending states, collapsible timeline. This gets the most visual attention (pain point #3).
- **Responsive**: sidebar → top bar + drawer under ~900px; `.field-grid` collapses to single column; `.table-wrap` gets horizontal scroll; toasts reposition for narrow screens.

## 9. Migration / rollout

1. Scaffold Vite + Preact under `apps/web` (new `src/`, `package.json`, `vite.config.js`); keep old `index.html`/`assets/*` in place until parity.
2. Build `api/client.js` + signals stores + `ui/` primitives.
3. Port pages one at a time to parity against the live API (Register → Logs → Accounts → Resources → Settings).
4. Point FastAPI mount at `dist`; add build step to deploy (`scripts/`), update `.gitignore` for `node_modules`/`dist` as appropriate.
5. Remove legacy `assets/app.js` + `assets/app.css` + old `index.html` once parity is verified.

## 10. Testing / verification

- **Parity checklist** per page: every button/endpoint from §5 exercised against a running control_api.
- **Build check**: `vite build` produces `dist/` served correctly by FastAPI mount.
- **Responsive check**: sidebar/table/grid at <900px.
- **Auth check**: login gate, cookie session, Bearer token path, logout — matches `tests/unit/test_control_api_login.py` expectations (frontend calls unchanged).
- Existing backend unit tests must remain green (no backend edits).

## 11. Risks

- **Deploy shift** (raw dir → `dist` build artifact): pxed deploy scripts + `.gitignore` must account for a build step. Mitigate by keeping legacy files until `dist` verified in place.
- **Parity gaps** in the large Register page (many advanced knobs). Mitigate with the §10 per-page checklist derived from current `index.html` field IDs.
- **Bundle/runtime regressions** vs current zero-dependency load. Preact keeps this small; verify final bundle size.

## 12. Open questions

- Deploy: build on pxed, or build locally and scp `dist`? (Decide during writing-plans.)
- Keep hash routing vs adopt a 2KB router lib? (Leaning hash, no dep.)
