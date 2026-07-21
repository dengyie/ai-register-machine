# Console UI · v1 进度元素合并实施计划


> **Status (2026-07-21):** Tasks A–F **implemented & committed** (`20380a3`…`33563d9`). Hand-test §9 + deploy checklist still open (do not disrupt live `batch_dc1k_ns`).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 console4 侧栏骨架上注入 v1 级进度可读性（KPI + 双进度条 + 步骤轨 + timeline + recent_writes），恢复 kind/product/SKIP_CLASH/SIZE/PAUSE/NODE_SCORE 启动面，统一账号/节点/邮箱/导入/设置的视觉语言；后端零/最小改动。

**Architecture:** 纯前端静态 vanilla HTML/CSS/JS。`apps/control_api/progress.py` 与 `run_status` 字段已就绪，UI 消费即可。仅 `runs.py` 可能新增一行 `recent_writes` 顶层扁平（避免 UI 深链嵌套 `progress` dict）。asset 版本 bump `?v=console5`。

**Tech Stack:** Python 3.13, FastAPI（后端仅 runs.py 微调）、vanilla JS/HTML/CSS 前端；无 Node/npm 构建；`grok_register` 仓库。

## Global Constraints

- **设计门禁：** 功能完备照 spec §3.4、观感照 spec §5.7；砍任一项都会回到「一半好看一半简陋」。
- **产品契约不动：** supervisor 硬编码 `CPA_REMOTE_INJECT=false` / `CPA_PROBE_CHAT=false`；批边界导入仅 `CPA_BATCH_END_INJECT` + EVERY/SIZE/PAUSE；主链路 disk-first 止于 `cpa_auths` 落盘。
- **fail-fast：** zero streak / stuck 必须在 KPI + header + bar 三处联动醒目；不加空转重试 UI。
- **stop 边界：** 仅 API stop → registry/lock pid + process group；前端**不引入** kill chromium / pkill 按钮。
- **不改 progress.py 相位机与 mint 契约。** 步骤轨读现有 `steps[]`。
- **不引入 npm/React/Vue/SSE**；仍 4s poll；`regFormDirty` 保护左侧表单不被 poll 清空。
- **不启用线上 CPA_BATCH_END_INJECT 默认 true。**
- **不停 coinbot，不打断线上 batch_dc1k_ns**（部署纯静态可热更；如同发 API 再考虑 pause）。
- **登录门不动：** cookie session + optional bearer；生产 `mango / …`；bootstrap admin 仅空用户库。
- **GitHub banner 保留** `https://github.com/dengyie/ai-register-machine`。
- **Deploy 模型 B：** in-repo；tar + scp pxed；无 rsync。
- **Repo path:** `/Users/mango/project/claude-project/grok-register`
- **Spec:** `docs/superpowers/specs/2026-07-21-console-ui-v1-progress-merge-design.md`（v2）
- **Baseline:** design commits `048ee94` + `1ee2d06`；code baseline console4 `apps/web/*?v=console4`；backend `apps/control_api/{progress,runs,routes_runs}.py`。

## File map

| Path | Role |
|------|------|
| `apps/web/index.html` | 注册页右栏 progress DOM 槽位、高级启动 `<details>`、导入 2×2、账号/节点 summary KPI 皮、品牌副标、`?v=console5` |
| `apps/web/assets/app.css` | `.kpi-grid` / `.kpi` / `.bar-row` / `.bar-track` / `.bar-fill` / `.step-rail` / `.step` / `.run-header` / `.run-writes` / `.timeline` / `.advanced` / `.import-grid` tokens 与规则；`.log-panel` flex + `#run-log flex:1` |
| `apps/web/assets/app.js` | `renderRunStatus` → 编排 `renderRunHeader` / `renderKpiGrid` / `renderBars` / `renderStepRail` / `renderStatusCard` / `renderRecentWrites` / `renderTimeline` / `renderRunHistory`；`startRun` 读高级启动全字段；kind/product 显隐；账号/节点 summary KPI 皮 |
| `apps/control_api/runs.py` | **仅一行**：`base` dict 加 `"recent_writes": progress.get("recent_writes") or []`（避免 UI 从 `run.progress.recent_writes` 深链）；不改契约 |
| `tests/unit/test_control_api_runs.py` | 加断言：`run_status()` 顶层含 `recent_writes` list |
| `scripts/deploy_web_console5.sh` | 可选：tar `apps/web` + `apps/control_api/runs.py` → scp pxed（人工触发） |

**不新增：** progress.py 字段、SSE、Node 构建、React 依赖、Turnstile 池按钮实现。

---

## Task A: DOM 槽位 + CSS tokens（骨架先立，静态可开无 JS 错）

**Rationale:** 先把 index.html 右栏由 `run-pills + status-card` 单调结构，换成 `#run-header / #run-kpi / #run-bars / #run-steps / #run-status-card / #run-writes / #run-timeline / log-toolbar / #run-log` 的完整骨架，配 CSS token；render 函数暂占位（写 innerHTML 空壳字符串）。此步只求「静态打开无 JS 报错、log 未被挤没」。

**Files:**
- Modify: `apps/web/index.html`
- Modify: `apps/web/assets/app.css`

**Interfaces produced:**
- 新 DOM ids: `#run-header`, `#run-kpi`, `#run-bars`, `#run-steps`, `#run-writes`, `#run-timeline`
- 新 CSS class: `.run-header`, `.kpi-grid`, `.kpi`, `.kpi .label/.value/.sub`, `.kpi.ok/.warn/.danger`, `.bar-row`, `.bar-track`, `.bar-fill`, `.bar-fill.warn/.danger`, `.bar-caption`, `.step-rail`, `.step`, `.step.done/.active/.pending`, `.run-writes`, `.timeline`, `.timeline-item`, `.chip.mini`, `.advanced`, `.import-grid`
- Asset version：`?v=console4` → `?v=console5`（HTML link/script 两处）

- [x] **Step 1: Replace right-panel DOM in `apps/web/index.html`**

在 `<section id="page-register">` 内、`<div class="panel log-panel">` 内，把现有

```html
<div class="run-pills" id="run-pills">
  <span class="pill">状态: —</span>
</div>
<div class="status-card" id="run-status-card">
  <div class="status-title" id="run-status-title">任务状态: —</div>
  <div class="status-body" id="run-status-body">尚未加载任务。</div>
</div>
<div class="log-toolbar">
  …
</div>
<pre id="run-log" …></pre>
```

替换为：

```html
<div id="run-progress" class="run-progress">
  <div id="run-header" class="run-header">
    <span class="status-dot" data-state="idle" aria-hidden="true"></span>
    <span class="status-word">idle</span>
    <span class="meta-chips" id="run-header-chips"></span>
  </div>
  <div id="run-kpi" class="kpi-grid"></div>
  <div id="run-bars" class="bars"></div>
  <div id="run-steps" class="step-rail"></div>
  <div id="run-status-card" class="status-card">
    <div class="status-title" id="run-status-title">任务状态: —</div>
    <div class="status-body" id="run-status-body">尚未加载任务。</div>
  </div>
  <div id="run-writes" class="run-writes hidden"></div>
  <details id="run-timeline-wrap" class="timeline-wrap" open>
    <summary>时间线</summary>
    <ol id="run-timeline" class="timeline"></ol>
  </details>
</div>

<div class="log-toolbar">
  <h2>运行日志</h2>
  <label class="inline">来源
    <select id="log-which">
      <option value="auto">auto</option>
      <option value="worker">worker</option>
      <option value="supervisor">supervisor</option>
      <option value="both">both</option>
    </select>
  </label>
  <label class="inline">行数
    <select id="log-tail">
      <option value="100">100</option>
      <option value="200" selected>200</option>
      <option value="500">500</option>
    </select>
  </label>
  <label class="check inline"><input type="checkbox" id="log-follow" checked /> follow</label>
  <span id="log-path" class="hint mono"></span>
</div>
<pre id="run-log" class="log"></pre>
```

**注意：**
- 保留 `id="run-status-card"` / `status-title` / `status-body` — Task B 会重用现有 render 分支。
- `log-follow` checkbox 保持既有 poll 语义（Task B 不改）。
- 顶部工具栏 `<div class="toolbar">` 内的按钮与既有 `#reg-form` 不动。

- [x] **Step 2: Bump asset version in `apps/web/index.html` (two places)**

```html
<link rel="stylesheet" href="/assets/app.css?v=console5" />
…
<script src="/assets/app.js?v=console5"></script>
```

- [x] **Step 3: Extend CSS in `apps/web/assets/app.css`**

在 `.run-pills` 规则之前保留旧 class（其它页仍可能用 `.pill`），追加以下段（可放文件末尾统一分组）：

```css
/* ── Run progress stack ─────────────────────────────── */
.run-progress {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  max-height: min(52vh, 28rem);
  overflow: auto;
  padding-right: 0.15rem;
}

.log-panel { display: flex; flex-direction: column; min-height: 0; }
.log-panel #run-log { flex: 1; min-height: 12rem; }

.run-header {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--panel);
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--border);
  border-radius: 9px;
  font-weight: 650;
}
.run-header .status-dot {
  width: 0.55rem; height: 0.55rem; border-radius: 999px;
  background: var(--muted);
}
.run-header[data-state="alive"] .status-dot { background: var(--ok); box-shadow: 0 0 0 3px rgba(91,212,154,0.15); }
.run-header[data-state="stuck"] {
  background: rgba(212,91,91,0.12);
  border-color: rgba(212,91,91,0.5);
}
.run-header[data-state="stuck"] .status-dot { background: var(--danger); }
.run-header .meta-chips { display: flex; gap: 0.35rem; flex-wrap: wrap; margin-left: auto; }
.run-header .status-word { letter-spacing: 0.03em; }

.chip.mini {
  font-size: 0.7rem;
  padding: 0.14rem 0.5rem;
  color: var(--muted);
}

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.55rem;
}
.kpi {
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 0.55rem 0.7rem;
  min-height: 4.25rem;
  display: flex; flex-direction: column; gap: 0.15rem;
}
.kpi .label {
  font-size: 0.72rem;
  color: var(--muted);
  letter-spacing: 0.02em;
  text-transform: uppercase;
}
.kpi .value {
  font-size: 1.35rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.kpi .sub { font-size: 0.75rem; color: var(--muted); }
.kpi.ok     { border-color: rgba(91,212,154,0.35); }
.kpi.warn   { border-color: rgba(212,177,91,0.4); }
.kpi.danger { border-color: rgba(212,91,91,0.45); background: rgba(212,91,91,0.06); }

.bars { display: flex; flex-direction: column; gap: 0.45rem; }
.bar-row {
  display: grid;
  grid-template-columns: 4.5rem 1fr auto;
  gap: 0.5rem;
  align-items: center;
}
.bar-row .bar-label { color: var(--muted); font-size: 0.78rem; }
.bar-track {
  height: 0.55rem;
  border-radius: 999px;
  background: rgba(255,255,255,0.06);
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, var(--accent-2), var(--ok));
  transition: width 0.35s ease;
}
.bar-fill.warn   { background: linear-gradient(90deg, var(--warn), #d48a5b); }
.bar-fill.danger { background: linear-gradient(90deg, #a04040, var(--danger)); }
.bar-caption {
  font-size: 0.78rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  min-width: 7rem;
  text-align: right;
}

.step-rail {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem 0.55rem;
  padding: 0.25rem 0;
}
.step {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.75rem;
  color: var(--muted);
}
.step::before {
  content: "";
  width: 0.6rem; height: 0.6rem; border-radius: 999px;
  border: 1px solid var(--muted);
  background: transparent;
}
.step.done { color: var(--ok); }
.step.done::before { background: var(--ok); border-color: var(--ok); }
.step.active { color: var(--accent); font-weight: 650; }
.step.active::before {
  background: var(--accent);
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgba(91,159,212,0.2);
  animation: step-pulse 1.4s ease-in-out infinite;
}
@keyframes step-pulse {
  0%,100% { box-shadow: 0 0 0 3px rgba(91,159,212,0.2); }
  50%     { box-shadow: 0 0 0 5px rgba(91,159,212,0.05); }
}
@media (prefers-reduced-motion: reduce) {
  .step.active::before { animation: none; }
  .bar-fill { transition: none; }
}

.run-writes {
  display: flex; flex-wrap: wrap; gap: 0.35rem;
  padding: 0.3rem 0.4rem;
  border: 1px dashed rgba(91,212,154,0.25);
  border-radius: 8px;
  background: rgba(91,212,154,0.03);
}
.run-writes .write-chip {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.72rem;
  color: var(--ok);
  padding: 0.12rem 0.45rem;
  border-radius: 999px;
  background: rgba(91,212,154,0.08);
}

.timeline-wrap summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 0.8rem;
  padding: 0.15rem 0;
}
.timeline {
  list-style: none;
  margin: 0.25rem 0 0;
  padding: 0 0 0 0.7rem;
  border-left: 2px solid rgba(91,159,212,0.3);
  display: flex; flex-direction: column; gap: 0.25rem;
}
.timeline-item {
  font-size: 0.76rem;
  color: var(--text);
  line-height: 1.35;
  display: flex; gap: 0.4rem; align-items: baseline;
}
.timeline-item .src {
  font-size: 0.66rem;
  padding: 0.05rem 0.35rem;
  border-radius: 999px;
  background: var(--panel-2);
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* ── Advanced start (Task D) ────────────────────────── */
.advanced {
  border: 1px solid var(--border);
  border-radius: 9px;
  padding: 0.35rem 0.6rem 0.6rem;
  background: var(--panel-2);
}
.advanced summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 0.85rem;
  padding: 0.2rem 0;
}
.advanced[open] { border-color: rgba(91,159,212,0.35); }
.advanced .field-grid { margin-top: 0.5rem; }

/* ── Import 2×2 (Task E) ────────────────────────────── */
.import-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(280px, 1fr));
  gap: 0.75rem;
}
@media (max-width: 900px) {
  .import-grid { grid-template-columns: 1fr; }
}

.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
```

**保留旧 `.run-pills`/`.pill` 规则**（其它页面例如 nodes group chip 可能复用）。

- [x] **Step 4: Verify skeleton loads without JS error**

- 本地临时改 `renderRunStatus` 首行 `return;` 或先注释掉调用，浏览器打开 `/index.html`：
  - 右栏无控制台报错
  - `#run-progress` 呈现空框架
  - 缩放窗口宽度到 <900px，`.split` 变单列（现有 media query 已处理）
- 恢复 render 调用（Task B 会替换实现，跳过临时短路）
- console 无 `Uncaught` 抛错

**Verification:**
- 打开页面登录后进入注册页：DOM 骨架完整、log 区可见（未被挤没）。
- 无 JS 报错、无 404（`?v=console5` 生效）。

---

## Task B: Render pipeline + poll + `recent_writes` 顶层扁平

**Rationale:** 让新骨架吃真实数据。`renderRunStatus` 从 pills-only 改为编排 header/kpi/bars/steps/card/writes/timeline；`refreshRegister` poll 契约不变（4s，不 reloadForm）；`runs.py` 加一行把 `recent_writes` 冒出顶层，UI 不必嵌套读 `run.progress.recent_writes`。

**Files:**
- Modify: `apps/web/assets/app.js`
- Modify: `apps/control_api/runs.py`
- Modify: `tests/unit/test_control_api_runs.py`

**Interfaces produced:**
- `renderRunHeader(run)` / `renderKpiGrid(run, overview)` / `renderBars(run)` / `renderStepRail(steps)` / `renderStatusCard(run)` / `renderRecentWrites(writes)` / `renderTimeline(items)`
- `run.recent_writes: string[]`（顶层）

- [x] **Step 1: Add `recent_writes` to `run_status` top-level base dict**

`apps/control_api/runs.py` 现有 `base` dict（约 line 63–91）尾部加：

```python
"recent_writes": progress.get("recent_writes") or [],
```

其它字段不动；`progress` 保留在 `base["progress"]` 保持兼容。

- [x] **Step 2: Add unit test in `tests/unit/test_control_api_runs.py`**

复用现有 fixture（root 临时目录 + 空 supervisor log 或 mock progress），追加：

```python
def test_run_status_flattens_recent_writes(tmp_path, monkeypatch):
    # arrange: minimal empty root; progress returns [] safely
    (tmp_path / "logs").mkdir()
    from apps.control_api.runs import run_status
    st = run_status(tmp_path)
    # None when no run + no lock + no progress → acceptable
    if st is not None:
        assert "recent_writes" in st
        assert isinstance(st["recent_writes"], list)
```

若既有测试已构造带 supervisor log 的 fixture（能触发 `alive` 分支），加一条断言即可。

- [x] **Step 3: Rewrite `renderRunStatus` in `apps/web/assets/app.js`**

删除 lines 331–375 的旧实现，替换为下列（保留同名以免动上游调用点）：

```javascript
function pct(a, b) {
  if (b == null || a == null || b <= 0) return null;
  return Math.max(0, Math.min(100, (Number(a) / Number(b)) * 100));
}

function fmtNum(v) {
  return v == null || v === "" ? "—" : String(v);
}

function renderRunHeader(run) {
  const el = $("#run-header");
  if (!el) return;
  const alive = !!(run && run.alive);
  const stuck = !!(run && run.stuck);
  el.dataset.state = stuck ? "stuck" : (alive ? "alive" : "idle");
  const word = alive ? "ALIVE" : (run ? "idle" : "无任务");
  el.querySelector(".status-word").textContent = word;
  const chips = [];
  const tag = run && (run.tag || (run.meta && run.meta.tag));
  if (tag) chips.push(`<span class="chip mini">tag=${escapeHtml(tag)}</span>`);
  if (run && run.pid != null) chips.push(`<span class="chip mini">pid=${run.pid}</span>`);
  if (run && run.mode) chips.push(`<span class="chip mini">${escapeHtml(run.mode)}</span>`);
  if (run && run.kind) chips.push(`<span class="chip mini">${escapeHtml(run.kind)}</span>`);
  if (stuck) chips.push(`<span class="chip mini danger" title="${escapeHtml(run.stuck_reason || "")}">stuck</span>`);
  $("#run-header-chips").innerHTML = chips.join("");
}

function renderKpiGrid(run, overview) {
  const el = $("#run-kpi");
  if (!el) return;
  const complete = run && run.complete != null ? run.complete : null;
  const goal = run && run.goal_complete != null ? run.goal_complete : null;
  const remain = run && run.remain != null ? run.remain : null;
  const gained = run && run.batch_gained != null ? run.batch_gained : null;
  const target = run && (run.target != null ? run.target : run.target_new);
  const sub = run && run.sub != null ? run.sub : null;
  const zero = run && run.consecutive_zero != null ? run.consecutive_zero : null;
  const disk = overview && overview.product_ok != null ? overview.product_ok : lastProductOk;
  const nodes = overview && overview.nodes ? overview.nodes : null;
  const alive = !!(run && run.alive);

  const zeroClass = run && run.stuck ? "danger" : (zero != null && zero >= 4 ? "warn" : "");
  const completeClass = alive && remain === 0 ? "ok" : "";

  el.innerHTML = [
    kpiCard("complete / goal", `${fmtNum(complete)}${goal != null ? " / " + goal : ""}`, remain != null ? `剩余 ${remain}` : "", completeClass),
    kpiCard("本批 gained", `${fmtNum(gained)}${target != null ? " / " + target : ""}`, run && run.batch_remain != null ? `剩余 ${run.batch_remain}` : "", ""),
    kpiCard("disk product_ok", fmtNum(disk), "", disk != null && disk > 0 ? "ok" : ""),
    kpiCard("sub · zero", `${fmtNum(sub)} · ${fmtNum(zero)}`, run && run.chunk != null ? `chunk ${run.chunk}` : "", zeroClass),
    kpiCard("mode", fmtNum(run && run.mode), run && run.kind ? escapeHtml(run.kind) : "", ""),
    kpiCard("nodes healthy", nodes ? `${fmtNum(nodes.healthy)} / ${fmtNum(nodes.total)}` : "—", nodes && nodes.enabled != null ? `enabled ${nodes.enabled}` : "", ""),
  ].join("");
}

function kpiCard(label, value, sub, cls) {
  const c = cls ? ` ${cls}` : "";
  const s = sub ? `<div class="sub">${escapeHtml(sub)}</div>` : "";
  return `<div class="kpi${c}"><div class="label">${escapeHtml(label)}</div><div class="value">${escapeHtml(value)}</div>${s}</div>`;
}

function renderBars(run) {
  const el = $("#run-bars");
  if (!el) return;
  const complete = run && run.complete != null ? run.complete : null;
  const goal = run && run.goal_complete != null ? run.goal_complete : null;
  const gained = run && run.batch_gained != null ? run.batch_gained : null;
  const target = run && (run.target != null ? run.target : run.target_new);
  const stuck = !!(run && run.stuck);
  const gp = pct(complete, goal);
  const bp = pct(gained, target);
  const gClass = stuck ? "danger" : "";
  const bClass = stuck ? "warn" : "";
  el.innerHTML = [
    barRow("全局", complete, goal, gp, gClass),
    barRow("本批", gained, target, bp, bClass),
  ].join("");
}

function barRow(label, a, b, pctVal, cls) {
  const fillStyle = pctVal == null ? "width:0" : `width:${pctVal.toFixed(1)}%`;
  const cap = pctVal == null ? "—" : `${fmtNum(a)} / ${fmtNum(b)} (${pctVal.toFixed(0)}%)`;
  const c = cls ? ` ${cls}` : "";
  return `<div class="bar-row">
    <span class="bar-label">${escapeHtml(label)}</span>
    <div class="bar-track"><div class="bar-fill${c}" style="${fillStyle}"></div></div>
    <span class="bar-caption">${escapeHtml(cap)}</span>
  </div>`;
}

function renderStepRail(steps) {
  const el = $("#run-steps");
  if (!el) return;
  if (!Array.isArray(steps) || !steps.length) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = steps
    .map((s) => `<span class="step ${escapeHtml(s.state || "pending")}" title="${escapeHtml(s.desc || "")}">${escapeHtml(s.title || s.id || "")}</span>`)
    .join("");
}

function renderStatusCard(run) {
  const title = $("#run-status-title");
  const body = $("#run-status-body");
  if (!title || !body) return;
  const alive = !!(run && run.alive);
  const phase = (run && run.phase_title) || (run && run.phase) || "—";
  title.textContent = `任务状态: ${alive ? "运行中" : run ? "空闲" : "—"} · ${phase}`;
  const lines = [];
  if (run) {
    if (run.phase_detail) lines.push(run.phase_detail);
    if (run.summary && run.summary.fatal_reason) lines.push(`fatal: ${run.summary.fatal_reason}`);
    if (run.worker_log) lines.push(`worker: ${run.worker_log}`);
    if (run.supervisor_log) lines.push(`supervisor: ${run.supervisor_log}`);
    if (run.stuck_reason && !run.summary?.fatal_reason) lines.push(`stuck: ${run.stuck_reason}`);
    if (!lines.length) lines.push("已加载当前任务。");
  } else {
    lines.push("当前无活动 supervisor。可在左侧填参数后点「开始」。");
  }
  body.textContent = lines.join("\n");
}

function renderRecentWrites(writes) {
  const el = $("#run-writes");
  if (!el) return;
  if (!Array.isArray(writes) || !writes.length) {
    el.classList.add("hidden");
    el.innerHTML = "";
    return;
  }
  el.classList.remove("hidden");
  el.innerHTML = writes
    .map((p) => {
      const name = String(p).split(/[\\/]/).pop() || String(p);
      return `<span class="write-chip" title="${escapeHtml(String(p))}">${escapeHtml(name)}</span>`;
    })
    .join("");
}

function renderTimeline(items) {
  const el = $("#run-timeline");
  if (!el) return;
  const cap = 6;
  const rows = Array.isArray(items) ? items.slice(-cap) : [];
  if (!rows.length) {
    el.innerHTML = `<li class="timeline-item hint">暂无事件</li>`;
    return;
  }
  el.innerHTML = rows
    .map((it) => {
      const src = escapeHtml(String(it.source || it.phase || "log"));
      const title = escapeHtml(String(it.title || ""));
      const line = escapeHtml(String(it.line || "").slice(0, 300));
      return `<li class="timeline-item"><span class="src">${src}</span><span>${title}${line ? ` · ${line}` : ""}</span></li>`;
    })
    .join("");
}

function renderRunStatus(run, productOk, overview) {
  renderRunHeader(run);
  renderKpiGrid(run, overview || { product_ok: productOk });
  renderBars(run);
  renderStepRail(run && run.steps);
  renderStatusCard(run);
  renderRecentWrites(run && run.recent_writes);
  renderTimeline(run && run.timeline);
}
```

- [x] **Step 4: Wire overview into `refreshRegister` (already dual-fetched)**

现有 `refreshRegister` 已经 `await api("/api/overview")` 拿到 `ov`；把 render 调用改为传整 `ov`：

```javascript
try {
  const ov = await api("/api/overview", { headers: headers() });
  lastProductOk = ov.product_ok;
  renderRunStatus(run, ov.product_ok, ov);
} catch {
  renderRunStatus(run, lastProductOk, null);
}
```

Poll 间隔 4s、`regFormDirty` 保护、`log-follow` 逻辑不动。

- [x] **Step 5: Verify progress renders live**

- 本地对 pxed control_api 反代或 stub 数据；或直接部署到 pxed 后浏览器看：
  - `#run-header` 显示 ALIVE + tag/pid/mode/kind chips
  - `#run-kpi` 6 卡；`sub·zero` 卡在 zero≥4 时黄边、stuck 时红底
  - `#run-bars` 全局与本批双条随 complete 增长（4s 内推进）
  - `#run-steps` 存在（`register_sh` 可能多 pending，可接受）
  - `#run-writes` 有 `xai-…json` 文件名 chip；空时隐藏
  - `#run-timeline` 展开 ≤6 行
  - `#run-log` `flex:1` 撑开，进度栈滚动、log 尾行可见

**Verification:**
- `uv run pytest tests/unit/test_control_api_runs.py -q` 全绿
- 前端浏览器 DevTools console 无报错
- poll 4s 期间左侧 `#reg-form` 编辑不被清空（regFormDirty 生效）

---

## Task C: stuck / zero 色态联动 + timeline 折叠 + sticky header

**Rationale:** Task B 完成后，色态已在 render 中打 class，但需**手测**联动无遗漏；且 timeline 展开默认 6 条并可折叠、header sticky 不遮挡 KPI。

**Files:**
- Modify: `apps/web/assets/app.js`（若手测发现 miss 才补）
- Modify: `apps/web/assets/app.css`（微调 sticky offset 与 z-index）

**Interfaces produced:** 无新接口；仅联动一致性。

- [x] **Step 1: Confirm联动矩阵**

| 条件 | 期望 UI |
|------|---------|
| `run.stuck === true` | header 红底 + status-dot red + `sub·zero` KPI `.danger` + 两条 bar-fill 红/黄 + status-card 显 `stuck_reason` 或 `summary.fatal_reason` |
| `run.consecutive_zero >= 4 && !stuck` | `sub·zero` KPI `.warn`；bar 保持默认色 |
| `run.alive && run.remain === 0` | `complete/goal` KPI `.ok` 绿边 |
| `disk product_ok > 0` | `disk` KPI `.ok` |
| `run.recent_writes` 空 | `#run-writes` 整块隐藏 |
| `steps` 空或 register_sh 未 flatten | `#run-steps` 内 innerHTML 清空但块保留（不 layout jump） |

如任一条不满足，回 Task B 修 render 逻辑。

- [x] **Step 2: Fine-tune sticky header**

`.log-panel` 内 `#run-progress` 滚动时 `.run-header` sticky 若被 `#run-log` 顶栏（`log-toolbar`）遮挡，加：

```css
.log-panel > .log-toolbar { position: sticky; top: 0; background: var(--panel); z-index: 1; padding: 0.35rem 0; }
```

（`run-progress` 与 `log-toolbar` 在同一 flex 容器；两者 sticky 各在自身滚动上下文里生效。）

- [x] **Step 3: Timeline default cap 6 + expand toggle (P2, 可选)**

已在 render 里 `slice(-6)`。若用户抱怨想看更多：把 `<details>` summary 加一个「全部」按钮：

```html
<summary>时间线 <button type="button" id="tl-expand" class="small">展开</button></summary>
```

```javascript
$("#tl-expand")?.addEventListener("click", () => {
  const cap = window.__tlCap === 24 ? 6 : 24;
  window.__tlCap = cap;
  // re-render on next poll or immediately from cached last run
});
```

**默认本 Task 不做**，仅记录钩子；若 §9 验收提「时间线太少」再启用。

**Verification:**
- 手动构造 stuck 情形（pxed 已有 `[supervisor] fatal:true` 情境）观察三处联动
- `#run-log` 滚动到底部时 `#run-header` 仍可见

---

## Task D: 高级启动完整字段 + kind/product 显隐 + startRun body

**Rationale:** 恢复 v1「kind + product + SKIP_CLASH_PREFLIGHT + NODE_SCORE + CPA_BATCH_IMPORT_SIZE/PAUSE」启动面，隐藏在 `<details class="advanced">` 折叠区，默认与今日生产一致。

**Files:**
- Modify: `apps/web/index.html`（在 `#reg-form` 末端）
- Modify: `apps/web/assets/app.js`（`startRun` + kind change 事件）

**Interfaces produced:**
- 新 DOM ids: `#reg-kind`, `#reg-product`, `#reg-skip-preflight`, `#reg-node-score`, `#reg-import-size`, `#reg-import-pause`, `#reg-sync-mail-env`
- `startRun()` body 支持 `kind ∈ {grok_supervisor, register_sh}`、`product ∈ {grok, mimo, chatgpt}`
- 显隐规则：kind=register_sh → 隐藏 `#reg-chunk` / `#reg-batch-end-inject` / `#reg-import-every` / `#reg-import-size` / `#reg-import-pause`；显示 `#reg-product`

- [x] **Step 1: Add `<details>` block to index.html**

在 `#reg-form` 内、最后一个 `.field-grid` 之后（`</form>` 之前）插入：

```html
<details class="advanced" id="reg-advanced">
  <summary>高级启动 · kind / product / SKIP_CLASH / NODE_SCORE / batch-end 波次</summary>
  <div class="field-grid">
    <label>kind
      <select id="reg-kind" name="kind">
        <option value="grok_supervisor" selected>grok_supervisor · 批量监督</option>
        <option value="register_sh">register_sh · 单次外壳</option>
      </select>
    </label>
    <label id="reg-product-row">product
      <select id="reg-product" name="product" disabled>
        <option value="grok" selected>grok</option>
        <option value="mimo">mimo</option>
        <option value="chatgpt">chatgpt</option>
      </select>
    </label>
    <label class="check" title="SKIP_CLASH_PREFLIGHT=1 会跳过 Clash 批前测活；默认关">
      <input type="checkbox" id="reg-skip-preflight" /> 跳过 Clash 批前测活 (SKIP_CLASH_PREFLIGHT)
    </label>
    <label>NODE_SCORE
      <select id="reg-node-score">
        <option value="" selected>(不传，吃环境)</option>
        <option value="1">1 · 打分排序</option>
        <option value="0">0 · 关闭打分</option>
      </select>
    </label>
    <label>CPA_BATCH_IMPORT_SIZE
      <input type="number" id="reg-import-size" min="1" placeholder="100" />
    </label>
    <label>CPA_BATCH_IMPORT_PAUSE
      <input type="number" id="reg-import-pause" min="0" placeholder="3" />
    </label>
    <label class="check span2" title="启动时把当前邮箱表单同步进 extra_env，避免 register_sh 不读 config">
      <input type="checkbox" id="reg-sync-mail-env" checked /> 同步 EMAIL_PROVIDER / DEFAULT_DOMAINS 到 extra_env
    </label>
  </div>
</details>
```

- [x] **Step 2: Add kind change listener & initial visibility**

在 `wireRegFormDirtyOnce` 之后（或与其它 register toolbar wiring 同区块）加：

```javascript
function applyKindVisibility() {
  const kind = $("#reg-kind")?.value || "grok_supervisor";
  const isSupervisor = kind === "grok_supervisor";
  const productSel = $("#reg-product");
  if (productSel) {
    productSel.disabled = isSupervisor;
    if (isSupervisor) productSel.value = "grok";
  }
  const toggles = [
    "#reg-chunk",
    "#reg-batch-end-inject",
    "#reg-import-every",
    "#reg-import-size",
    "#reg-import-pause",
  ];
  toggles.forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    const wrap = el.closest("label");
    if (wrap) wrap.style.display = isSupervisor ? "" : "none";
  });
}
$("#reg-kind")?.addEventListener("change", applyKindVisibility);
applyKindVisibility();
```

- [x] **Step 3: Rewrite `startRun` extra_env / body**

替换 lines 419–457：

```javascript
async function startRun() {
  const pre = $("#reg-action-result");
  try { await saveRegisterCfg(); } catch { /* continue */ }

  const kind = $("#reg-kind")?.value || "grok_supervisor";
  const product = kind === "grok_supervisor" ? "grok" : ($("#reg-product")?.value || "grok");
  const extra_env = {};

  // supervisor-only knobs
  if (kind === "grok_supervisor") {
    const chunk = ($("#reg-chunk")?.value || "").trim();
    if (chunk) extra_env.SUPERVISOR_CHUNK = chunk;
    extra_env.CPA_BATCH_END_INJECT = $("#reg-batch-end-inject")?.checked ? "true" : "false";
    const every = ($("#reg-import-every")?.value || "").trim();
    if (every) extra_env.CPA_BATCH_IMPORT_EVERY = every;
    const size = ($("#reg-import-size")?.value || "").trim();
    if (size) extra_env.CPA_BATCH_IMPORT_SIZE = size;
    const pause = ($("#reg-import-pause")?.value || "").trim();
    if (pause !== "") extra_env.CPA_BATCH_IMPORT_PAUSE = pause;
  }

  // universal probe-off (supervisor hard-forces false again; register_sh honors)
  extra_env.CPA_PROBE_CHAT = "false";

  // advanced
  if ($("#reg-skip-preflight")?.checked) extra_env.SKIP_CLASH_PREFLIGHT = "1";
  const nodeScore = ($("#reg-node-score")?.value || "").trim();
  if (nodeScore !== "") extra_env.NODE_SCORE = nodeScore;

  // sync mail env (P1)
  if ($("#reg-sync-mail-env")?.checked) {
    const prov = ($("#reg-email-provider")?.value || "").trim();
    const dom = ($("#reg-domains")?.value || "").trim();
    if (prov) extra_env.EMAIL_PROVIDER = prov;
    if (dom) extra_env.DEFAULT_DOMAINS = dom;
  }

  const body = {
    kind,
    product,
    mode: $("#reg-mode")?.value || "ordinary",
    target: Number($("#reg-target")?.value || 100),
    threads: Number($("#reg-threads")?.value || 1),
    tag: ($("#reg-tag")?.value || "batch_web").trim() || "batch_web",
    extra_env,
  };
  try {
    const data = await api("/api/runs/start", {
      method: "POST",
      headers: headers(true),
      body: JSON.stringify(body),
    });
    setResult(pre, data);
    await refreshRegister({ reloadForm: false });
  } catch (e) {
    setResult(pre, String(e.message || e));
  }
}
```

- [x] **Step 4: stop result 显示 source/mode（若 API 返回）**

`stopRun` 已经 `setResult(pre, data)`，`RunActionOut` 已带 `pid/source/mode`（见 `runs.py:290–297`）。手测确认 `data.source` / `data.mode` 出现在结果 pre 中 — 若 detail 覆盖，改：

```javascript
setResult(pre, { ok: data.ok, source: data.source, mode: data.mode, detail: data.detail });
```

- [x] **Step 5: Verify allowlist round-trip**

- 打开高级启动，勾 `SKIP_CLASH_PREFLIGHT`，设 `NODE_SCORE=1` / `SIZE=50` / `PAUSE=5`
- 点开始，观察 `#reg-action-result` JSON `run.extra_env` 含全部键
- kind 切 register_sh：CHUNK / batch-end 行消失、product select enabled
- 后端 `EXTRA_ENV_ALLOWLIST` 现已含 `SKIP_CLASH_PREFLIGHT / NODE_SCORE / CPA_BATCH_IMPORT_SIZE / CPA_BATCH_IMPORT_PAUSE / EMAIL_PROVIDER / DEFAULT_DOMAINS`；越界值 API 会 400，UI `setResult` 显示

**Verification:**
- 默认（未展开高级）启动 body = 今日生产：`kind=grok_supervisor, product=grok, mode=ordinary, extra_env` 含 `CPA_PROBE_CHAT=false + CPA_BATCH_END_INJECT=false + CHUNK`
- 切 register_sh + mimo 启动一次，日志出现对应 product handler
- `test_control_api_runs.py` 若有 allowlist round-trip 断言仍绿

---

## Task E: 运维页 KPI 皮 + 导入 2×2 + 品牌副标

**Rationale:** 只改注册页会让「一半好看一半土」持续。账号/节点 summary 改成 `.kpi-grid.compact` 单行三格；导入四卡改 2×2；品牌副标改为 multi-product。

**Files:**
- Modify: `apps/web/index.html`（导入 4 卡包 `.import-grid`，账号/节点 summary 结构）
- Modify: `apps/web/assets/app.js`（`refreshAccounts` summary render → KPI；nodes summary 同）
- Modify: `apps/web/assets/app.css`（`.kpi-grid.compact`）

**Interfaces produced:** 无新 API；`#acc-summary` / `#nodes-catalog-summary` / `#clash-summary` innerHTML 结构变。

- [x] **Step 1: Import 2×2 in `apps/web/index.html`**

把 `<section id="page-import">` 内 4 张 `<div class="card">` 用 `<div class="import-grid">` 包住：

```html
<section id="page-import" class="page">
  <header class="page-head">
    <div>
      <h1>导入</h1>
      <p class="hint">节点文件 / 邮件凭证 / auth 包 / config pack。默认 auth 导入不远程 tebi。</p>
    </div>
  </header>
  <div class="import-grid">
    <div class="card"> <!-- Nodes/proxies 文件 --> … </div>
    <div class="card"> <!-- Mail credentials --> … </div>
    <div class="card"> <!-- Account / token dumps --> … </div>
    <div class="card"> <!-- Config pack (zip) --> … </div>
  </div>
  <pre id="import-result" class="log"></pre>
</section>
```

- [x] **Step 2: Account summary → KPI row**

在 `apps/web/index.html` `#page-accounts` 里把 `<div id="acc-summary" class="card muted">—</div>` 换成：

```html
<div id="acc-summary" class="kpi-grid compact"></div>
```

`app.js` `refreshAccounts` 里 `sum.textContent = …` 替换为：

```javascript
const c = data.complete || 0;
const t = data.total || 0;
const inc = Math.max(0, t - c);
const dc = data.disk_complete != null ? data.disk_complete : null;
sum.innerHTML = [
  kpiCard("total (本页)", String(t), `path=${escapeHtml(data.path || "")}`, ""),
  kpiCard("complete", String(c), `第 ${data.page}/${data.pages || 1} 页`, c > 0 ? "ok" : ""),
  kpiCard("incomplete", String(inc), "", inc > 0 ? "warn" : ""),
  dc != null ? kpiCard("disk complete", String(dc), "全盘", "ok") : "",
].filter(Boolean).join("");
sum.className = "kpi-grid compact";
```

（`kpiCard` 是 Task B 已加的辅助函数；`compact` 变体见 Step 4。）

- [x] **Step 3: Nodes summary skin**

`#nodes-catalog-summary` 与 `#clash-summary` 保持既有 `<span>` 文案即可（信息密度已可），或同样切 KPI 皮；若时间紧，最小实现只加 `.kpi-grid.compact` 到 catalog summary 里，clash 保留 hint span。**推荐先只做 accounts + import**，nodes 留到验收有余力再做，避免过度膨胀。

- [x] **Step 4: Add `.kpi-grid.compact` CSS**

`app.css` 追加：

```css
.kpi-grid.compact {
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
}
.kpi-grid.compact .kpi { min-height: 3.25rem; padding: 0.4rem 0.55rem; }
.kpi-grid.compact .kpi .value { font-size: 1.05rem; }
```

- [x] **Step 5: Brand subtitle multi-product**

`index.html` 两处「Grok 注册机」：
- `<title>`：`AI 注册机 · Control Plane`（保留原字样也可，取舍轻，**默认改**）
- 侧栏 `.side-title`：`AI 注册机`；副行 `<a class="side-link">grok / mimo / chatgpt</a>` 或保留仓库链
- 登录卡 `<h1>登录 Control Plane` 不动

**Verification:**
- 账号页 summary 变 3–4 卡；`complete>0` 绿边、`incomplete>0` 黄边
- 导入页 2×2 grid，`<900px` 单列
- 侧栏副标不再写死 Grok
- 现有 accounts 单测 `test_control_api_accounts.py` 仍绿（API 未动）

---

## Task F: 历史 list_runs 折叠区 + 手测清单 §9

**Rationale:** 恢复 v1 Runs 面「历史运行」信息（`GET /api/runs`），并按设计 §9 走一遍手测清单。

**Files:**
- Modify: `apps/web/index.html`（`#page-register` 底部）
- Modify: `apps/web/assets/app.js`（`renderRunHistory` + poll 拉取或按需拉取）

**Interfaces produced:** `renderRunHistory(runs: [{path, name, mtime}])`

- [x] **Step 1: Add `<details>` in `#page-register` bottom**

在 `.split` div 关闭之后、`</section>` 之前：

```html
<details id="run-history-wrap" class="card">
  <summary>最近运行（supervisor logs）</summary>
  <div id="run-history" class="hint">加载中…</div>
</details>
```

- [x] **Step 2: Render function**

`app.js` 加：

```javascript
async function refreshRunHistory() {
  const box = $("#run-history");
  if (!box) return;
  try {
    const data = await api("/api/runs", { headers: headers() });
    const runs = Array.isArray(data.runs) ? data.runs : [];
    if (!runs.length) {
      box.innerHTML = `<span class="hint">暂无历史 supervisor 日志。</span>`;
      $("#run-history-wrap")?.classList.add("hidden");
      return;
    }
    $("#run-history-wrap")?.classList.remove("hidden");
    box.innerHTML =
      `<ul class="run-history-list">` +
      runs.slice(0, 12)
        .map((r) => {
          const d = new Date((r.mtime || 0) * 1000);
          const iso = isNaN(d.getTime()) ? "—" : d.toISOString().replace("T", " ").slice(0, 19);
          return `<li><span class="mono hint">${escapeHtml(iso)}</span> · <span class="mono">${escapeHtml(r.name || "")}</span></li>`;
        })
        .join("") +
      `</ul>`;
  } catch {
    /* silent */
  }
}
```

CSS 追加：

```css
.run-history-list { list-style: none; margin: 0.4rem 0 0; padding: 0; display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.78rem; }
.run-history-list li { color: var(--muted); }
```

调用点：只在 `<details>` 首次展开时触发（避免 4s poll 空刷）：

```javascript
$("#run-history-wrap")?.addEventListener("toggle", (e) => {
  if (e.target.open) refreshRunHistory();
});
```

不加入 4s poll。

- [x] **Step 3: 手测清单 §9 走一遍**

按 spec §9.1–9.3 逐条勾：

- [ ] 9.1 注册页同屏可见 ALIVE / KPI / bars / step / phase / recent_writes / timeline / log tail
- [ ] 9.1 steps active 与 phase 一致（±1 poll 延迟）
- [ ] 9.1 stuck 或 zero≥4 时无需读 log 已能看出危险
- [ ] 9.1 `#run-log` 高度 ≥ 12rem
- [ ] 9.1 空数据显 `—`，无假 0% 满条
- [ ] 9.2 高级启动能发 register_sh + mimo/chatgpt；SIZE/PAUSE/SKIP_CLASH/NODE_SCORE 进 `extra_env`
- [ ] 9.2 默认开始仍 grok_supervisor + grok + `CPA_PROBE_CHAT=false` + batch-end 默认 false
- [ ] 9.2 4s poll 不清空左侧表单编辑
- [ ] 9.2 账号 / 邮箱 / 节点 / 导入 / 设置功能无回退
- [ ] 9.2 导入四卡 2×2，账号 summary KPI 皮
- [ ] 9.2 `GET /api/runs` 历史折叠区存在
- [ ] 9.2 stop 结果含 source/mode
- [ ] 9.3 KPI/bar/step 使用 §5.7 token；无营销 hero、无假进度动画
- [ ] 9.3 品牌副标 multi-product
- [ ] 9.3 文案不宣称 mid-mint tebi 注入

**Verification:**
- 部署到 pxed 后走一遍手测清单
- 所有既有 pytest 单测绿：`uv run pytest tests/unit -q`

---

## Deploy checklist

- [ ] 本地 `uv run pytest tests/unit -q` 全绿
- [ ] `apps/web/index.html` / `app.css` / `app.js` 更新且 `?v=console5`
- [ ] `apps/control_api/runs.py` 多一行 `recent_writes`
- [ ] 打包：`cd /Users/mango/project/claude-project/grok-register && tar czf /tmp/console5.tgz apps/web apps/control_api/runs.py tests/unit/test_control_api_runs.py`
- [ ] 若线上 `batch_dc1k_ns` 仍在跑：**不要**重启 supervisor；纯静态 UI 无需 pause，`runs.py` 改动仅在 control_api 进程重启时生效
- [ ] `scp /tmp/console5.tgz pxed:/tmp/` → ssh 解压到 `/personal/grok-register`（symlink 到 `/data/grok-register` 结构不变）
- [ ] 只重启 control_api：`ssh pxed 'pkill -f "apps.control_api" && cd /personal/grok-register && nohup uv run python -m apps.control_api > logs/control_api.log 2>&1 &'`（若 systemd 托管则 `systemctl restart …`）
- [ ] 浏览器打开 `http://pxed:8787` → 登录 `mango / …` → 走 §9 手测

## Rollback

- 保留旧 asset 版本 `?v=console4` 拷贝：部署前 `cp apps/web/assets/app.js apps/web/assets/app.js.console4.bak` 便于回滚
- 后端仅一行 `recent_writes` 扁平；回滚删该行即可
- `?v=console5` → `?v=console4` 一次替换即可整站回到当前版本

## Out of scope（记录，不做）

- SSE 替代 poll
- Turnstile 池按钮接入
- 独立 Overview sidebar 页（默认并入注册页 KPI）
- 多 product complete 分计数（MiMo/ChatGPT sinks）
- `progress.py` 相位机重写
- `mihomo` flock 继承坑（运维层）

---

## Execution modes

- **Subagent-Driven Development（推荐）：** 每 Task A–F 交给 `superpowers:subagent-driven-development` 一个 subagent，主线只 review & commit。适合并行 review。
- **Inline sequential：** 主线按 A→B→C→D→E→F 顺序推进，每 Task 结束 commit + verify。适合小改动、需要现场 UI 眼睛看。

用户选择模式后再动 UI 代码。
