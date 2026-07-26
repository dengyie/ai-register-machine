# Console10 第二批（IA/交互）Implementation Plan

> 前置：第一批（结构重构，2026-07-26-console10-refactor-batch1.md）已完成且全绿。
> 本批**允许视觉/交互变化**，但不改后端契约、不改保存语义（PUT body、confirm 拦截逻辑不变）。

**Goal:** 按第一批登记的 9 条 IA 待办改善 console10 信息架构与交互表达。

**用户已拍板的取舍：**
- 待办 3（raw JSON）：6 处 `<pre class="log">` → 折叠 `<details>`（默认收起，summary 一行摘要）。
- 待办 8（三层 tab）：Nodes 子 tab 提升到资源页顶层 → 「节点·Clash / 节点·catalog / 邮箱 / 导入」四平铺。
- 待办 5（池规则）：新建状态行组件表达 email_providers 两页不对称规则，替换 4 段 hint 长文字。

## Global Constraints

- 不改任何 PUT/POST body、不改 confirm 拦截条件与文案语义（文案可精简但拦截行为不变）。
- 不动 Python / 后端。
- 不 commit、不 push、不部署 —— 除非用户明确要求。
- 每个任务后 `npm test && npm run build` 全绿；现有 45 个测试若因 IA 改动断言失效，先改测试再改实现。
- 双指标 UI（注册页 progress）不动。

## Tasks

### Task B1: `ui/LogDetails.jsx` 折叠日志组件（待办 3）
- Create `src/ui/LogDetails.jsx`：`LogDetails({ text, summary, compact })` → 无 text 时 null；否则 `<details class="log-details">` + `<summary>`（默认「响应详情」或调用方给的摘要）+ 原 `<pre class="log …">`。
- 替换 6 处：SettingsPage、ImportTab、MailTab、AccountsPage、ClashPanel、CatalogPanel。
- styles/components.css 加 `.log-details` 样式（summary 一行、hint 色、展开后 pre 原样）。

### Task B2: 资源页四平铺 tab（待办 8）
- ResourcesPage TABS → `[clash 节点·Clash, catalog 节点·catalog, mail 邮箱, import 导入]`，直接渲染 `<ClashPanel active>` / `<CatalogPanel active>`（保持常驻挂载 + active 语义）。
- 删除 NodesTab.jsx 壳与其子 Tabs；`resources.css` 里 `.nodes-tab` 选择器改为面板容器 class（`nodes-panels` 包一层保留原样式命中）。

### Task B3: `ProviderPoolStatus` 状态行组件（待办 5）
- Create `src/ui/ProviderPoolStatus.jsx`：props `{ selected, primaryProvider, canClear, hydrated, dirty }` → chips 显示当前池、空池时显示「单通道 <primary>」、`canClear` 显示「此页可清空池」/「此页不可清池（去设置页）」徽标、未加载/未保存警示。
- SettingsPage、RegisterPage(RegForm) 接入，删除对应 hint 长段。

### Task B4: MailTab IA（待办 1+2）
- defaultDomains 提升：MailProviderPanels 里 CF/CloudMail 两处 textarea 收敛为全局区一个控件（`mail/DefaultDomainsCard.jsx`），CF 勾选仍写同一 form 字段；面板内只留说明短句。
- 配置区（Provider 面板 + 保存）与运维区（凭证导入 / 池探测，破坏性操作）之间加分区标题，运维区标注「操作立即生效，不走保存」。

### Task B5: Settings 分组 + 术语（待办 4）
- settings-form 分三组：邮箱链路 / 代理 / 注册行为；`cpa_probe_chat` → 「注册后探测 chat（cpa_probe_chat）」、`cpa_remote_inject (intent)` → 「远端注入意向（cpa_remote_inject）」、`turnstile_stuck_timeout` → 「Turnstile 卡死超时秒（turnstile_stuck_timeout）」等：中文名 + code 原名。
- 保存语义不变。

### Task B6: Register「自检 →」（待办 6）
- footer `<a class="btn btn-ghost btn-sm">` → 文字链接样式 `class="link-hint"`（新样式：hint 色下划线），文案「自检 / 清理 → 设置页」。

### Task B7: CSS 收敛（待办 7）
- run.css 611 行拆出 `run-progress.css`（RunProgress 专属段）；跨文件重复的 `.page-head/.toolbar`（run.css vs base.css 双定义）去重，保 base.css 版本。
- 仅删被覆盖的重复定义，不改最终计算样式。

### Task B8: CI 给 PR 跑 web 测试（待办 9）
- ci.yml 新增独立 `web-test` job（无 main-only `if`，needs 无、push+PR 都跑）：setup-node + `npm ci && npm test`（working-directory apps/web）。package-web 保持不变。

## 收尾
`npm test && npm run build` 全绿后报告；不 commit / push / deploy。
