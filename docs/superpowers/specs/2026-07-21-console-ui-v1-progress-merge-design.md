# Console UI · v1 进度元素合并设计

**Date:** 2026-07-21  
**Status:** Draft — awaiting user review before implementation  
**Product:** ai-register-machine Web control plane (`apps/web` + `apps/control_api`)  
**Related:** `docs/superpowers/specs/2026-07-21-web-control-plane-design.md`（基线架构仍有效）  
**Baseline commits / surfaces:**
- **v1（c44ac22）:** top-nav Overview / Config / Import / Runs；状态卡片 + JSON 预览
- **console4（当前）:** sidebar 注册 / 账号池 / 邮箱接码 / 节点池 / 导入 / 设置；右侧 pills + status-card + log
- **Backend 已就绪:** `apps/control_api/progress.py` → `build_progress` 已产出 `steps[]` / `timeline[]` / KPI 计数，UI 未消费

---

## 1. Problem

console 重写保留了更好的 **IA 骨架**（侧栏多页、注册表单 + 日志分栏、账号/节点/邮箱运维面），但把 **进度呈现** 退化成：

| 当前 console4 | 问题 |
|---------------|------|
| `run-pills` 一行 chip | 信息密度低，没有「还差多少 / 完成百分比」的视觉权重 |
| `status-card` 纯文本 phase | 有 `phase_title`/`phase_detail`，但没有步骤轨、没有双进度条 |
| 无 KPI 网格 | `complete/goal`、`batch_gained/target`、`product_ok`、`remain` 后端有，前端未做卡片 |
| 无 step rail | `GET /api/runs/current` 已 flatten `steps[]`（done/active/pending），UI 完全忽略 |
| 无 timeline | `timeline[]` 已有，UI 未展示 |
| 丢 v1 启动面 | kind / product / `SKIP_CLASH_PREFLIGHT` 在 API 与 v1 Runs 表单存在，console 写死 `grok_supervisor`+`grok` |

v1 的优点不是「截图里的配色」，而是 **一眼能读完任务健康度**（status card 一行：`product_ok · run=ALIVE · complete · zero`）+ **Runs 启动参数完整**。console 的优点是 **骨架与运维页**。本设计只做 **注入与归位**，不重做整站视觉、不生搬参考图。

---

## 2. Goals / Non-goals

### 2.1 Goals

1. **保留 console 骨架**：sidebar 六页、注册 split（左表单 / 右状态+日志）、账号池 / 邮箱 / 节点 / 导入 / 设置。
2. **注入 v1 级进度可读性**：KPI 卡片 + 双进度条 + 步骤轨 + 精简 timeline，全部吃现有 `build_progress` / `run_status` 字段，**不新开后端契约**（除非 UI 缺字段再补，见 §6）。
3. **恢复 v1 丢掉的控制面能力**（不丢产品功能）：
   - multi-kind 启动：`grok_supervisor` | `register_sh`
   - multi-product：`grok` | `mimo` | `chatgpt`（`register_sh` 时）
   - `SKIP_CLASH_PREFLIGHT` 表面（allowlist 已有）
   - Overview 级「全局一眼」信息（可并入注册页顶栏，不必强行加第七页，见 §4.1）
4. **产品契约硬约束 UI 不得反向诱导**：
   - mint 路径 `CPA_REMOTE_INJECT=false` / `CPA_PROBE_CHAT=false`（supervisor 硬编码）
   - 批边界导入仅 `CPA_BATCH_END_INJECT` + EVERY knobs
   - disk-first：主链路止于 `cpa_auths` 落盘
   - fail-fast：zero  streak / stuck 必须醒目
   - 不全局 `pkill`；stop 只打 registry/lock pid + process group
5. **静态 vanilla HTML/CSS/JS**，无 Node 构建；asset 版本 bump（如 `?v=console5`）。

### 2.2 Non-goals

- 不做参考截图像素级复刻 / 换皮肤大赛
- 不引入 React/Vue/npm
- 不重写 `progress.py` 相位机（可微调文案，不改算法优先级）
- 不做 job 队列 / 多 host / 实时 SSE 强制升级（仍 4s poll；可选后续）
- 不启用线上 `CPA_BATCH_END_INJECT` 默认 true
- 不改生产登录 `mango / …`；bootstrap admin 仍仅空用户库
- 不把 coinbot / 无关进程纳入 stop 范围
- 不做 Turnstile 浏览器池接入（工具栏 WIP 按钮保持 disabled）

---

## 3. Feature inventory

### 3.1 v1（c44ac22）有、console 弱化或丢失

| 能力 | v1 | console4 | 本设计 |
|------|----|----------|--------|
| Overview hub | 独立页：status card + full JSON | 无独立页；注册页偶尔拉 overview 只取 `product_ok` | **注册页顶 KPI + 可选折叠 JSON**；不强制新 sidebar 项 |
| 状态一行可读 | `product_ok · ALIVE · complete · zero` | pills 散点 | **KPI 网格 + 进度条** 取代纯 pills 为主展示 |
| kind 选择 | `grok_supervisor` / `register_sh` | 写死 supervisor | **高级启动折叠区** 恢复 |
| product 选择 | grok/mimo/chatgpt | 写死 grok | **register_sh 时显示 product** |
| SKIP_CLASH_PREFLIGHT | 表单字段 | 无 | **高级启动折叠区** checkbox |
| Runs 独立页 | 有 | 合并进「注册」 | **保持合并**（骨架优先）；启动控件在注册左栏 |
| Config 页 | 精简字段 | 设置 + 注册表单双写 | **保持** 设置全量 + 注册协议子集 |

### 3.2 console 已有、必须保留

| 页 | 保留内容 |
|----|----------|
| 注册 | 邮箱/域名/target/threads/mode/tag、Turnstile 超时、CHUNK、sso-only、proxy_rotate、proxy_list、batch-end inject + EVERY、probe 强制关、开始/停止/刷新、log which/tail/follow |
| 账号池 | complete 列表、筛选分页、soft-delete |
| 邮箱接码 | provider/domains/secrets、Hotmail 凭证导入、plus-alias 禁默认 |
| 节点池 | Clash 叶子 + catalog 双 tab、测活、筛选 |
| 导入 | nodes / mail / auths / pack（auth 默认 no-remote） |
| 设置 | config 全量、bearer token、selfcheck/cleanup |
| 登录门 | cookie session + optional bearer |

### 3.3 后端已提供、前端未用（本设计消费）

来自 `run_status` / `build_progress`（已 flatten 到 `GET /api/runs/current` 的 `run`）：

| 字段 | UI 用途 |
|------|---------|
| `alive`, `pid`, `kind`, `meta.tag` / `tag` | 状态 pill + KPI |
| `complete`, `goal_complete`, `baseline_complete`, `remain` | 总进度条 + KPI「全局 complete」 |
| `batch_gained`, `target`/`target_new`, `batch_remain` | 本批进度条 + KPI「本批」 |
| `consecutive_zero`, `sub`, `chunk`, `mode` | KPI / warn pill |
| `phase`, `phase_title`, `phase_detail` | status-card 标题与副文案 |
| `stuck`, `stuck_reason` | danger 态 + 顶栏告警 |
| `steps[]` `{id,title,desc,state}` | **步骤轨** |
| `timeline[]` `{source,phase,title,line}` | **时间线**（最近 N 条） |
| `recent_writes` | 可选小列表「最近落盘」 |
| `supervisor_log`, `worker_log` | 路径 hint（已有） |
| overview.`product_ok`, `nodes.{total,enabled,healthy}` | KPI disk + 节点健康摘要 |

---

## 4. Page-by-page layout（骨架不变）

### 4.1 注册页（主战场）

**骨架：** `page-head` toolbar 不变；`split` = 左 `form-panel` + 右 `log-panel`。

#### 4.1.1 右侧：进度栈（注入点）

自上而下固定顺序（替换/增强现有 pills + status-card 区域）：

```
┌─ run-header ─────────────────────────────────────────┐
│ [ALIVE|idle]  tag=…  pid=…  mode=ordinary            │
│ (stuck 时整条红底：stuck_reason)                       │
└──────────────────────────────────────────────────────┘
┌─ kpi-grid (2×3 或 3×2) ──────────────────────────────┐
│ complete/goal   │ 本批 gained/target │ disk product_ok │
│ remain          │ sub · zero         │ nodes healthy*  │
└──────────────────────────────────────────────────────┘
┌─ bars ───────────────────────────────────────────────┐
│ 全局 complete ████████░░░░  630 / 1190  (53%)          │
│ 本批   gained  ██░░░░░░░░  2 / 562                      │
└──────────────────────────────────────────────────────┘
┌─ step-rail (横向可换行) ─────────────────────────────┐
│ ●批次  ●节点  ●子批  ●浏览器  ◉表单  ○盾  ○OTP …     │
│ state: done=绿勾  active=高亮脉冲  pending=灰          │
└──────────────────────────────────────────────────────┘
┌─ status-card ────────────────────────────────────────┐
│ phase_title                                          │
│ phase_detail（单行截断，hover/展开全文）               │
└──────────────────────────────────────────────────────┘
┌─ timeline (可折叠，默认展开最近 8 条) ────────────────┐
│ · supervisor | mint | wrote xai-…                    │
│ · worker | otp | …                                   │
└──────────────────────────────────────────────────────┘
┌─ log-toolbar + #run-log (现有) ──────────────────────┐
```

\* `nodes healthy` 来自 overview，poll 失败时显示 `—`，不阻塞进度渲染。

**pills 去留：** 不再作为主信息源；可保留 1 行极简 chips（ALIVE / stuck / mode）或完全并入 `run-header`。**禁止**再把 complete/goal 只塞进小 pill。

**百分比计算（前端纯函数）：**

```
goalPct   = goal>0 && complete!=null ? clamp(complete/goal*100,0,100) : null
batchPct  = target>0 && gained!=null ? clamp(gained/target*100,0,100) : null
```

`null` 时条显示 indeterminate/空轨 + 文案 `—`，不伪造 0%。

#### 4.1.2 左侧：启动参数恢复

在现有 `reg-form` **底部**（或「数量/线程/模式」区块旁）增加 **「高级启动」`<details>`**，默认折叠：

| 控件 | 绑定 | 说明 |
|------|------|------|
| kind | `select` | `grok_supervisor`（默认）/ `register_sh` |
| product | `select` | grok/mimo/chatgpt；**仅 kind=register_sh 时启用**，supervisor 时锁 grok |
| SKIP_CLASH_PREFLIGHT | checkbox | → `extra_env.SKIP_CLASH_PREFLIGHT=1|0` |
| NODE_SCORE | optional select 0/1 | allowlist 已有；默认不传（吃环境/配置） |

`startRun()` body 改为读这些控件；**默认值与今日生产一致**（supervisor + grok + 不 skip preflight），避免误触。

现有 batch-end / CHUNK / probe 契约不变：

- 保存配置仍强制 `cpa_remote_inject=false`、`cpa_probe_chat=false`
- `CPA_BATCH_END_INJECT` 只走 `extra_env`，不写 config intent 为 true 当默认

#### 4.1.3 顶栏「全局一眼」（v1 Overview 精神）

不强制新 sidebar「Overview」页（避免和注册抢注意力）。在注册 `page-head` hint 行或 KPI 第一格体现：

`product_ok=N · run=ALIVE|idle · complete=C/G · zero=Z`

与 KPI 网格同源数据，避免第三套计数。

**可选（P2）：** 设置页或导入页不显示 live run；若用户强烈需要独立 Overview，再加 sidebar「总览」——本设计 **默认不做**，以减少页数。

### 4.2 账号池 / 邮箱 / 节点 / 导入 / 设置

**本轮仅样式一致性小修**（卡片间距、表格、danger 按钮对齐进度区 token），**不改业务逻辑**。

可选增强（P2，不阻塞主交付）：

- 账号池 summary 卡与 KPI 同款数字样式
- 节点页 summary 与 overview.nodes 数字对齐
- 导入结果 pre 保持

### 4.3 不生搬参考图

| 参考图可能有的 | 我们的选择 |
|----------------|------------|
| 大面积插画 / 营销 hero | **不要** |
| 多列复杂 dashboard widget | **只要** KPI + 双 bar + step rail |
| 仿 Grok 官网字体/紫粉渐变 | **沿用 console CSS 变量**（`--bg/--panel/--ok/--danger`） |
| 假进度动画 | **只绑定真实 complete/gained**；无数据不转圈骗进度 |
| 把「协议注册」做成向导多 step 表单 | **保留单页表单**；步骤轨只描述 **运行管线** |

---

## 5. Component specs（前端）

全部 vanilla：`app.js` 渲染函数 + `app.css` class。无新依赖。

### 5.1 `renderKpiGrid(run, overview)`

- 容器 `#run-kpi`（新建）
- 每卡：`label` + `value` + 可选 `sub`
- stuck 时 zero 卡加 `.danger`
- alive 时 complete 卡加 `.ok` 边框（轻量）

### 5.2 `renderBars(run)`

- `#run-bars`
- 两条 `.bar-row`：label、track、fill（width%）、caption `a / b`
- `prefers-reduced-motion` 下取消 fill transition

### 5.3 `renderStepRail(steps)`

- `#run-steps`
- 水平 flex wrap；每步 `.step` + `.done|.active|.pending`
- active 显示 title；pending 可只显示短 title
- 空 `steps` 时隐藏整轨

### 5.4 `renderTimeline(timeline)`

- `#run-timeline`，默认 cap 8；「展开全部」到 24（API 已 limit 24）
- 每行：`source` chip + `title` + 截断 `line`
- 与 log tail **互补**：timeline 过滤后的人话事件，log 是原文

### 5.5 `renderRunStatus` 重写

现有 pills-only 函数改为编排：

```
renderRunHeader → renderKpiGrid → renderBars → renderStepRail
→ status-card 文案 → renderTimeline
```

**Poll 契约保持：** `refreshRegister({reloadForm:false})` 每 4s；**禁止** poll 重载表单（`regFormDirty` 逻辑保留）。

### 5.6 CSS tokens

在现有 console 变量上扩展即可：

```css
--bar-track: …;
--bar-fill: var(--ok or accent);
--step-done / --step-active / --step-pending
```

不新增第二套主题文件。

---

## 6. Backend

### 6.1 默认：零 API 变更

`GET /api/runs/current` 与 `GET /api/overview` 字段已够用。

### 6.2 仅当实现中发现缺口再加（非本设计必须）

| 缺口 | 处理 |
|------|------|
| `tag` 不在 flatten 顶层 | 读 `meta.tag` 或 progress 解析；缺则 UI `—` |
| overview 无 goal | 不强制 overview 带 progress；注册页以 runs/current 为准 |
| `register_sh` progress 弱 | step rail 可能多 pending；status-card 仍显示 last lines — 可接受 |

**禁止**为本 UI 改 supervisor 日志格式或 mint 契约。

---

## 7. Product / security invariants（UI 验收必须过）

1. **disk-first：** 文案与默认勾选继续强调「只产落盘」；batch-end inject 默认关。
2. **probe：** supervisor 路径 UI 保持 disabled + 说明强制 false。
3. **start 409：** 锁占用时结果区展示 API detail，不连环重试。
4. **stop：** 仅 API stop；前端不引入「杀全部 chromium」按钮。
5. **auth：** 401 → 登录门；poll 不刷爆登录错误 toast。
6. **secrets：** 设置/邮箱 key 掩码逻辑不变。
7. **plus-alias：** 邮箱页默认不勾选；文案保留「生产勿开」。
8. **healthy-only import：** 导入页 auth 默认 no-remote；不在 UI 推广盲注入。

---

## 8. Implementation plan（实现阶段，本文件只设计）

| Phase | 内容 | 验收 |
|-------|------|------|
| **A** | DOM 槽位 + CSS（kpi/bars/steps/timeline）空壳 | 静态打开无 JS 错误 |
| **B** | `renderRunStatus` 接真实 fields；poll 刷新 | 本地 mock 或 pxed 只读：条与数字随 complete 动 |
| **C** | 高级启动 kind/product/SKIP_CLASH | start body 正确；默认路径与现网一致 |
| **D** | stuck/zero 视觉 + timeline 折叠 | zero≥4 或 stuck 红态可见 |
| **E** | 回归：表单 dirty、probe 强制、batch-end env | 现有 unit 不破；手测注册页 |

顺序建议 **A→B→D→C→E**（先可读性，后启动面）。

**文件预期：**

- 修改：`apps/web/index.html`、`apps/web/assets/app.js`、`apps/web/assets/app.css`
- 可选测试：前端无自动化则用 control_api 既有 pytest；可加轻量 HTML fixture 或纯 JS 百分比函数的 node-less 测（若项目无 runner则手测清单）
- **不改** `progress.py` 除非发现 bug

**部署：** 与此前 control plane 相同 tar+scp pxed；**部署前 pause supervisor 可选**——纯静态 UI 热更通常不需 pause；若同发 API 再 pause。

---

## 9. Acceptance criteria

1. 注册页右侧能同时看到：**ALIVE、complete/goal 条、本批 gained/target 条、step rail 当前步、phase 文案、log tail**。
2. `steps[]` 的 active 步与 `phase` 一致（允许 log 延迟 1 个 poll）。
3. stuck 或 consecutive_zero≥4 时危险态无需读 log 也能发现。
4. 高级启动可发起 `register_sh` + mimo/chatgpt，且 `SKIP_CLASH_PREFLIGHT` 进入 allowlist env。
5. 默认开始仍为 `grok_supervisor` + grok + disk-first env。
6. 4s poll **不**重置左侧表单编辑。
7. 账号/邮箱/节点/导入/设置功能回归无回退。
8. 无参考图装饰性大图、无假进度。
9. 文档与 UI 文案不宣称 mid-mint tebi 注入。

---

## 10. Mapping: v1 精神 → console 落点

| v1 精神 | console 落点 |
|---------|--------------|
| Overview 一行 status card | 注册 KPI + run-header |
| Overview JSON | 可选 `<details>`「原始 run JSON」调试用（P2） |
| Runs 表单 kind/product/skip | 注册左栏「高级启动」 |
| Runs log tall | 右栏现有 log（保留 which/tail/follow） |
| Config 页 | 设置 + 注册协议子集（已有） |
| Import 四卡 | 导入页（已有，保留） |

---

## 11. Open questions（需用户拍板则标）

设计默认已选定，无需阻塞文档；若反对再改：

1. **独立 Overview 页？** 默认 **否**（KPI 进注册页）。若要「总览」sidebar，实现阶段加一页即可。
2. **timeline 默认展开？** 默认 **展开 8 条**；若嫌吵可默认折叠。
3. **NODE_SCORE UI？** 默认高级区可选；不传则后端/环境默认。

---

## 12. Out of scope follow-ups（记录，不进本交付）

- flock 被 mihomo 继承导致假锁（运维坑，非 UI）
- SSE 替代 poll
- Turnstile 池接入工具栏按钮
- 多 product complete 分计数（MiMo/ChatGPT sinks）
- register_core 迁移外壳与 UI 文案同步（另一 milestone）

---

## 13. Summary

**做：** 在 console4 侧栏骨架上，把 v1 的「一眼进度」做成 KPI + 双进度条 + 步骤轨 + timeline，并恢复 kind/product/SKIP_CLASH 启动面；吃满已有 `build_progress`。  
**不做：** 换皮抄图、新框架、改 mint/CPA 契约、删运维页。  
**下一步：** 用户审阅本 spec → 通过后写 `docs/superpowers/plans/2026-07-21-console-ui-v1-progress-merge.md` 再动 UI 代码。
