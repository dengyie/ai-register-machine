# Console10 重构 · 第一批（结构，零视觉变化）实施计划

> 第二批（IA / 交互）另开文档。本批的验收标准是：**operator 在浏览器里看不出任何差别**。

**Goal:** 消除 console10 前端的重复代码与超大文件，建立前端测试基线，为第二批 IA 改造铺路。

**Architecture:** 先搭 Vitest 并对现有行为写回归测试（红线），再做纯机械抽取与文件拆分，测试全程保持绿。

**Tech Stack:** Preact 10 + Vite 6 + @preact/signals；新增 vitest + @testing-library/preact + jsdom（devDependencies）。

## Global Constraints

- **零视觉变化**：不改任何 JSX 输出结构、class 名、文案、按钮顺序。
- **零行为变化**：hydrated / dirty / baseline / clearable confirm 语义逐字保持；两页各自的 confirm 文案**分别保留**，不统一。
- `apps/web/src/lib/providers.js` 的导出签名不变（后端与测试都依赖）。
- 不动 `apps/control_api/**` 与任何 Python 文件。
- 不 commit、不 push、不部署 —— 除非用户明确要求。
- CI 现有 `package-web` job 必须继续绿；新增的 web 测试要挂进 CI。

---

## Task 1: 搭 Vitest 基线

**Files:**
- Modify: `apps/web/package.json`
- Create: `apps/web/vitest.config.js`
- Create: `apps/web/src/lib/providers.test.js`

**Interfaces:**
- Produces: `npm test` 脚本；`vitest.config.js` 导出 jsdom 环境 + preact preset。

- [ ] **Step 1: 装依赖**

```bash
cd apps/web
npm i -D vitest@^4 jsdom@^26 @testing-library/preact@^3
```

- [ ] **Step 2: 写 vitest.config.js**

```js
import { defineConfig } from "vitest/config";
import preact from "@preact/preset-vite";

export default defineConfig({
  plugins: [preact()],
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{js,jsx}"],
  },
});
```

- [ ] **Step 3: package.json 加 script**

```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 4: 给已有的 providers.js 写测试（验证工具链通）**

```js
import { describe, it, expect } from "vitest";
import {
  normalizeProviderName,
  normalizeProvidersList,
  providersFromConfig,
  EMAIL_PROVIDERS,
} from "./providers.js";

describe("normalizeProviderName", () => {
  it("maps outlook aliases to hotmail", () => {
    expect(normalizeProviderName("Outlook")).toBe("hotmail");
    expect(normalizeProviderName("microsoft")).toBe("hotmail");
  });
  it("maps google aliases to gmail", () => {
    expect(normalizeProviderName("GoogleMail")).toBe("gmail");
  });
});

describe("normalizeProvidersList", () => {
  it("parses comma / space / full-width separators", () => {
    expect(normalizeProvidersList("gmail, hotmail　cloudflare"))
      .toEqual(["gmail", "hotmail", "cloudflare"]);
  });
  it("dedupes and drops unknown", () => {
    expect(normalizeProvidersList(["gmail", "gmail", "bogus"])).toEqual(["gmail"]);
  });
  it("accepts arrays", () => {
    expect(normalizeProvidersList(EMAIL_PROVIDERS)).toEqual(EMAIL_PROVIDERS);
  });
  it("empty in → empty out", () => {
    expect(normalizeProvidersList("")).toEqual([]);
    expect(normalizeProvidersList(null)).toEqual([]);
  });
});

describe("providersFromConfig", () => {
  it("prefers multi over singleton", () => {
    expect(providersFromConfig({ email_providers: ["gmail"], email_provider: "hotmail" }))
      .toEqual(["gmail"]);
  });
  it("falls back to singleton when multi empty", () => {
    expect(providersFromConfig({ email_providers: [], email_provider: "hotmail" }))
      .toEqual(["hotmail"]);
  });
});
```

- [ ] **Step 5: 跑测试**

Run: `cd apps/web && npm test`
Expected: PASS, 8 tests.

- [ ] **Step 6: commit**

```bash
git add apps/web/package.json apps/web/package-lock.json apps/web/vitest.config.js apps/web/src/lib/providers.test.js
git commit -m "test(web): add vitest baseline + providers.js unit tests"
```

---

## Task 2: 抽 `lib/http.js`（pretty / auth401），消除 4 份重复

**Files:**
- Create: `apps/web/src/lib/http.js`
- Create: `apps/web/src/lib/http.test.js`
- Modify: `apps/web/src/pages/Settings/SettingsPage.jsx`（删 28-34、36-42，改 import）
- Modify: `apps/web/src/pages/Resources/MailTab.jsx`（删 31-37、109-115）
- Modify: `apps/web/src/pages/Resources/NodesTab.jsx`（删 9-15、17-23）
- Modify: `apps/web/src/pages/Resources/ImportTab.jsx`（删 9-15、30-36）

**Interfaces:**
- Produces: `pretty(v) -> string`、`auth401(e) -> boolean`（副作用：401 时置 `session.value.authenticated = false`）。
- 命名用 `auth401` 而非 `auth`，避免与页面内其他局部变量撞名，也让调用点自解释。

- [ ] **Step 1: 写测试（先红）**

```js
import { describe, it, expect, beforeEach } from "vitest";
import { pretty, auth401 } from "./http.js";
import { session } from "../store/session.js";

beforeEach(() => {
  session.value = { authenticated: true, checked: true };
});

describe("pretty", () => {
  it("passes strings through unchanged", () => {
    expect(pretty("hi")).toBe("hi");
  });
  it("pretty-prints objects with 2-space indent", () => {
    expect(pretty({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
  it("falls back to String() on circular refs", () => {
    const o = {};
    o.self = o;
    expect(pretty(o)).toBe("[object Object]");
  });
});

describe("auth401", () => {
  it("returns false and leaves session alone for non-401", () => {
    expect(auth401({ status: 500 })).toBe(false);
    expect(session.value.authenticated).toBe(true);
  });
  it("returns false for null", () => {
    expect(auth401(null)).toBe(false);
  });
  it("returns true and clears authenticated on 401", () => {
    expect(auth401({ status: 401 })).toBe(true);
    expect(session.value.authenticated).toBe(false);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/web && npx vitest run src/lib/http.test.js`
Expected: FAIL — `Failed to resolve import "./http.js"`

- [ ] **Step 3: 实现 lib/http.js**

```js
// Shared API-call helpers: JSON pretty-printing + 401 session handling.
// Extracted from the four copies previously inlined in Settings/Mail/Nodes/Import.
import { session } from "../store/session.js";

/** JSON.stringify with 2-space indent; strings pass through; never throws. */
export function pretty(v) {
  try {
    return typeof v === "string" ? v : JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

/** On a 401 error, drop the session so LoginGate shows. Returns true if handled. */
export function auth401(e) {
  if (e && e.status === 401) {
    session.value = { ...session.value, authenticated: false };
    return true;
  }
  return false;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd apps/web && npx vitest run src/lib/http.test.js`
Expected: PASS, 6 tests.

- [ ] **Step 5: 改四个页面的调用点**

每个文件：删掉本地 `pretty` / `auth` 定义，加 `import { pretty, auth401 } from "../../lib/http.js";`，把所有 `auth(e)` 改成 `auth401(e)`。
`SettingsPage.jsx` 里 `session` 的 import 若只被 `auth` 用则一并删除（用 grep 确认）。

- [ ] **Step 6: 全量测试 + build**

```bash
cd apps/web && npm test && npm run build
```
Expected: 测试全绿；build 产出 dist/index.html。

- [ ] **Step 7: commit**

```bash
git add apps/web/src/lib/http.js apps/web/src/lib/http.test.js apps/web/src/pages
git commit -m "refactor(web): extract shared pretty/auth401 into lib/http.js"
```

---

## Task 3: 抽 `useBusy` hook，把 37 处裸字符串 busy 收敛

**Files:**
- Create: `apps/web/src/lib/useBusy.js`
- Create: `apps/web/src/lib/useBusy.test.jsx`
- Modify: `SettingsPage.jsx` / `MailTab.jsx` / `NodesTab.jsx` / `ImportTab.jsx` / `AccountsPage.jsx`

**Interfaces:**
- Produces: `useBusy() -> { busy, is(tag), any, run(tag, fn) }`
  - `busy`: 当前 tag 字符串或 `""`
  - `is(tag)`: 布尔，等价于旧的 `busy === "tag"`
  - `any`: 布尔，等价于 `busy !== ""`
  - `run(tag, fn)`: `setBusy(tag)` → `await fn()` → `finally setBusy("")`，返回 fn 的返回值，异常原样抛出

- [ ] **Step 1: 写测试（先红）**

```jsx
import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/preact";
import { useBusy } from "./useBusy.js";

function Probe({ onReady }) {
  const b = useBusy();
  onReady(b);
  return <span data-testid="busy">{`${b.busy}|${b.is("save")}|${b.any}`}</span>;
}

describe("useBusy", () => {
  it("starts idle", () => {
    render(<Probe onReady={() => {}} />);
    expect(screen.getByTestId("busy").textContent).toBe("|false|false");
  });

  it("sets tag during run and clears after", async () => {
    let api;
    render(<Probe onReady={(b) => { api = b; }} />);
    let release;
    const p = api.run("save", () => new Promise((r) => { release = r; }));
    await waitFor(() =>
      expect(screen.getByTestId("busy").textContent).toBe("save|true|true"),
    );
    release("done");
    await expect(p).resolves.toBe("done");
    await waitFor(() =>
      expect(screen.getByTestId("busy").textContent).toBe("|false|false"),
    );
  });

  it("clears the tag and rethrows when fn throws", async () => {
    let api;
    render(<Probe onReady={(b) => { api = b; }} />);
    await expect(api.run("save", async () => { throw new Error("boom"); }))
      .rejects.toThrow("boom");
    await waitFor(() =>
      expect(screen.getByTestId("busy").textContent).toBe("|false|false"),
    );
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/web && npx vitest run src/lib/useBusy.test.jsx`
Expected: FAIL — 无法解析 `./useBusy.js`

- [ ] **Step 3: 实现**

```js
// Single-slot busy-tag state. Replaces the setBusy("tag") / busy === "tag"
// string pairs that were duplicated across every ops page.
import { useCallback, useState } from "preact/hooks";

export function useBusy(initial = "") {
  const [busy, setBusy] = useState(initial);
  const is = useCallback((tag) => busy === tag, [busy]);
  const run = useCallback(async (tag, fn) => {
    setBusy(tag);
    try {
      return await fn();
    } finally {
      setBusy("");
    }
  }, []);
  return { busy, setBusy, is, any: busy !== "", run };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd apps/web && npx vitest run src/lib/useBusy.test.jsx`
Expected: PASS, 3 tests.

- [ ] **Step 5: 逐页替换**

只做机械替换：`const [busy, setBusy] = useState("")` → `const { busy, setBusy, is: busyIs, run: withBusy } = useBusy();`，
`busy === "x"` → `busyIs("x")`。**`setBusy` 保留导出**，先不强制把每个 try/finally 改写成 `withBusy`——
只在 try/finally 结构完全标准（`setBusy(t)` 开头、`finally { setBusy("") }` 结尾、中间无其他 setBusy）的函数里改写。
结构特殊的（如 NodesTab 的 `testAllCatalog` 用 `setCatResult("testing…")` 而非 busy）保持原样。

- [ ] **Step 6: 测试 + build + commit**

```bash
cd apps/web && npm test && npm run build
cd ../.. && git add apps/web/src && git commit -m "refactor(web): centralize busy-tag state in useBusy hook"
```

---

## Task 4: 抽 `useConfigForm` hook —— hydrated / dirty / baseline / clearable confirm

**这是本批风险最高的一步。先写回归测试锁住现有行为，再抽。**

**Files:**
- Create: `apps/web/src/lib/useConfigForm.js`
- Create: `apps/web/src/lib/useConfigForm.test.jsx`
- Modify: `apps/web/src/pages/Settings/SettingsPage.jsx`
- Modify: `apps/web/src/pages/Resources/MailTab.jsx`

**Interfaces:**
- Consumes: `lib/http.js` 的 `auth401`
- Produces:
```
useConfigForm({ empty, hydrate, buildPartial, confirmClear, onSaved, onError })
  -> { form, set, setForm, hydrated, dirty, baseline, load({force}), save(), busy }
```
  - `hydrate(config) -> { form, baseline }`：各页自己把 `/api/config` 映射成表单 + baseline
  - `buildPartial(form) -> object`：各页自己产出 PUT 的 partial
  - `confirmClear(form, baseline) -> string[] | null`：返回将被清空的字段描述列表；hook 负责弹 `window.confirm`，文案由各页通过 `confirmMessage(list)` 提供

- [ ] **Step 1: 给现状写回归测试（对 SettingsPage 与 MailTab 各一组）**

覆盖这五条不变量（当前两页都成立，抽取后必须仍成立）：
1. 初始 GET 失败 → `hydrated=false` → Save 按钮 disabled，点击不发 PUT。
2. GET 401 → `session.authenticated=false` 且 `hydrated=false`。
3. 有 dirty 时点 Reload → 弹 confirm；取消则不发 GET。
4. 把 clearable 字段清空后点 Save → 弹 confirm；取消则不发 PUT。
5. Save 成功 → `dirty=false`，表单以服务端返回值重新水合。

测试用 `vi.mock("../../api/client.js")` 打桩，断言 `putConfig` 的调用次数与入参。

- [ ] **Step 2: 跑测试确认全绿（锁住现状，此时还没抽 hook）**

Run: `cd apps/web && npm test`
Expected: PASS —— 这批测试描述的是**现有**行为，应当立刻通过。若有一条不通过，说明我对现状的理解有误，停下来核对，不要改测试去迁就。

- [ ] **Step 3: 实现 useConfigForm.js**

- [ ] **Step 4: SettingsPage 切到 hook**

Run: `cd apps/web && npm test`
Expected: Step 1 的 Settings 那组测试仍全绿。

- [ ] **Step 5: MailTab 切到 hook**（`baselineDomains` 字符串按现状塞进 `baseline` 对象的一个字段，**confirm 文案不变**）

Run: `cd apps/web && npm test`
Expected: 全绿。

- [ ] **Step 6: build + commit**

```bash
cd apps/web && npm run build
cd ../.. && git add apps/web/src && git commit -m "refactor(web): share hydrate/dirty/clearable-confirm logic via useConfigForm"
```

---

## Task 5: 拆 MailTab（1023 行 → 5 个文件）

**Files:**
- Create: `apps/web/src/pages/Resources/mail/MailProviderPanels.jsx`（CF/CloudMail/DuckMail/yyds/Gmail/Hotmail 六个折叠面板 + `panel()` helper）
- Create: `apps/web/src/pages/Resources/mail/MailCredImport.jsx`（凭证导入卡片）
- Create: `apps/web/src/pages/Resources/mail/MailPoolProbe.jsx`（池 KPI + 探测 + 精简 + 移出 + 结果表）
- Create: `apps/web/src/pages/Resources/mail/domains.js`（`parseDomainList` / `joinDomains`）
- Create: `apps/web/src/pages/Resources/mail/domains.test.js`
- Modify: `apps/web/src/pages/Resources/MailTab.jsx`（降到 ~200 行：状态 + 编排 + 三个子组件）

**Interfaces:**
- `MailProviderPanels({ form, set, openPanels, onTogglePanel, cfDomains, cfMeta, cfSelected, onFetchCfDomains, onToggleCfDomain, busy })`
- `MailCredImport({ onImported })` —— 自管 credText/credMode/busy
- `MailPoolProbe({ pool, onReloadPool })` —— 自管 probe/selected/busy
- `domains.js`: `parseDomainList(raw) -> string[]`、`joinDomains(list) -> string`（去重保序、大小写不敏感）

- [ ] **Step 1: domains.js 测试先行**

```js
import { describe, it, expect } from "vitest";
import { parseDomainList, joinDomains } from "./domains.js";

describe("parseDomainList", () => {
  it("splits on comma, full-width comma, and whitespace", () => {
    expect(parseDomainList("a.com，b.com c.com")).toEqual(["a.com", "b.com", "c.com"]);
  });
  it("returns [] for empty/null", () => {
    expect(parseDomainList("")).toEqual([]);
    expect(parseDomainList(null)).toEqual([]);
  });
});

describe("joinDomains", () => {
  it("dedupes case-insensitively, keeping first spelling and order", () => {
    expect(joinDomains(["A.com", "b.com", "a.COM"])).toBe("A.com,b.com");
  });
  it("drops blanks", () => {
    expect(joinDomains(["", "  ", "a.com"])).toBe("a.com");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**（模块不存在）

- [ ] **Step 3: 从 MailTab.jsx:56-76 原样搬运两个函数到 domains.js 并导出**

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: 依次搬三个子组件**

每搬一个：MailTab 只保留 `<MailPoolProbe … />` 之类的调用；搬完立刻 `npm run build` 确认编译通过。
搬运是**剪切粘贴**，不重排 JSX、不改 class、不改文案。

- [ ] **Step 6: 全量测试 + build + commit**

```bash
cd apps/web && npm test && npm run build
cd ../.. && git add apps/web/src && git commit -m "refactor(web): split MailTab into provider panels / cred import / pool probe"
```

---

## Task 6: 拆 NodesTab（989 行 → 4 个文件）

**Files:**
- Create: `apps/web/src/pages/Resources/nodes/ClashPanel.jsx`（订阅导入 + 池测活 + 策略组 + 叶子表）
- Create: `apps/web/src/pages/Resources/nodes/CatalogPanel.jsx`（添加节点 + 筛选分页 + catalog 表）
- Create: `apps/web/src/pages/Resources/nodes/clashFilters.js`（`filterClashLeaves` / `namesInPool` / 各 OPTS 常量）
- Create: `apps/web/src/pages/Resources/nodes/clashFilters.test.js`
- Modify: `apps/web/src/pages/Resources/NodesTab.jsx`（降到 ~60 行：子 tab 切换 + 两个 panel）

- [ ] **Step 1: clashFilters 测试先行**

```js
import { describe, it, expect } from "vitest";
import { filterClashLeaves, namesInPool } from "./clashFilters.js";

const LEAVES = [
  { name: "hk-1", health: "fail", in_register_pool: true,  last_delay_ms: 100, groups: ["🎯Grok注册"] },
  { name: "us-2", health: "ok",   in_register_pool: true,  last_delay_ms: 300, groups: ["🎯Grok注册"] },
  { name: "jp-3", health: "ok",   in_register_pool: false, last_delay_ms: 50,  groups: ["PROXY"] },
  { name: "sg-4", health: "unknown", in_register_pool: true, last_delay_ms: null, groups: [] },
];

describe("filterClashLeaves", () => {
  it("pool mode keeps only register-pool leaves", () => {
    expect(filterClashLeaves(LEAVES, "pool", "").map((n) => n.name))
      .toEqual(["us-2", "sg-4", "hk-1"]);
  });
  it("sorts pool-first, then ok > unknown > fail, then by delay", () => {
    expect(filterClashLeaves(LEAVES, "all", "").map((n) => n.name))
      .toEqual(["us-2", "sg-4", "hk-1", "jp-3"]);
  });
  it("query filters by case-insensitive substring", () => {
    expect(filterClashLeaves(LEAVES, "all", "JP").map((n) => n.name)).toEqual(["jp-3"]);
  });
  it("does not mutate the input array", () => {
    const before = LEAVES.map((n) => n.name);
    filterClashLeaves(LEAVES, "all", "");
    expect(LEAVES.map((n) => n.name)).toEqual(before);
  });
});

describe("namesInPool", () => {
  it("matches by group membership", () => {
    expect(namesInPool({ leaves: LEAVES }, "PROXY")).toEqual(["jp-3"]);
  });
  it("falls back to in_register_pool for known register groups", () => {
    expect(namesInPool({ leaves: LEAVES }, "♻️Grok优选"))
      .toEqual(["hk-1", "us-2", "sg-4"]);
  });
  it("honours the limit", () => {
    expect(namesInPool({ leaves: LEAVES }, "♻️Grok优选", 2)).toHaveLength(2);
  });
});
```

> 注：期望的排序结果在 Step 2 跑之前先按 `NodesTab.jsx:25-43` 的比较器手推一遍；如果实际输出与上面不符，**以实现为准修正测试**（本任务是保行为，不是改行为），并在 commit message 里记一句。

- [ ] **Step 2: 跑测试确认失败**（模块不存在）

- [ ] **Step 3: 从 NodesTab.jsx:25-84、173-185 搬运到 clashFilters.js**

- [ ] **Step 4: 跑测试确认通过**

- [ ] **Step 5: 搬两个 panel 组件，每次搬完 build**

- [ ] **Step 6: 全量测试 + build + commit**

```bash
cd apps/web && npm test && npm run build
cd ../.. && git add apps/web/src && git commit -m "refactor(web): split NodesTab into clash / catalog panels"
```

---

## Task 7: 抽 `PageHeader` 组件

**Files:**
- Create: `apps/web/src/ui/PageHeader.jsx`
- Modify: `apps/web/src/ui/index.js`
- Modify: 五个页面的 `<header class="page-head">`

**Interfaces:**
- `PageHeader({ title, hint, children })` → `children` 渲染进 `.toolbar`

- [ ] **Step 1: 实现**

```jsx
// Shared page header: title + hint on the left, toolbar slot on the right.
export function PageHeader({ title, hint, children }) {
  return (
    <header class="page-head">
      <div>
        <h1>{title}</h1>
        {hint ? <p class="hint">{hint}</p> : null}
      </div>
      {children ? <div class="toolbar">{children}</div> : null}
    </header>
  );
}
```

- [ ] **Step 2: 逐页替换**

注意：`SettingsPage` 与 `ResourcesPage` 的 hint 里含 `<a>` / `<code>` —— 用 JSX 片段作为 `hint` 传入，**不要**改成纯文本。
`ResourcesPage` 没有 toolbar，不传 children。

- [ ] **Step 3: build 并肉眼 diff 产物 HTML 结构**

Run: `cd apps/web && npm run build && npm test`
Expected: 全绿；DOM 结构与替换前逐字一致。

- [ ] **Step 4: commit**

```bash
git add apps/web/src && git commit -m "refactor(web): extract shared PageHeader component"
```

---

## Task 8: web 测试挂进 CI

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: 在 `package-web` job 的 "Build console10" 之前插入测试步骤**

```yaml
      - name: Web unit tests
        working-directory: apps/web
        run: |
          npm ci
          npm test
```

- [ ] **Step 2: 本地干跑等价命令**

```bash
cd apps/web && npm ci && npm test && npm run build
```
Expected: 全绿。

- [ ] **Step 3: commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(web): run console10 unit tests before packaging"
```

> `package-web` 只在 push 到 main 时跑（`if: github.ref == 'refs/heads/main'`）。
> 若希望 PR 也跑 web 测试，需要另开一个不带该 `if` 的 job —— 本批不做，记入第二批待议。

---

## 收尾

全部任务完成后：`npm test && npm run build`，然后向用户报告 diff 规模与文件行数前后对比。
**不 commit 到远端、不部署**，等用户确认后再走 push / deploy 流程。

## 第二批（IA）待办清单 —— 本批不动，只登记

1. `defaultDomains` 三个控件合一，从 provider 折叠面板里提到全局区。
2. MailTab 配置区 / 运维区分离（保存型 vs 破坏型操作）。
3. 六处 raw JSON `<pre class="log">` 的去留。
4. Settings 平铺表单分组；`cpa_remote_inject (intent)` 等内部术语改写。
5. `email_providers` 两页不对称规则的 UI 化表达（现在靠 4 段 hint 文字）。
6. Register footer「自检 →」链接伪装成按钮。
7. 41 个跨文件重复 class 的 CSS 收敛；`run.css` 611 行拆分。
8. 资源页三层 tab 嵌套。
9. CI 里给 PR 也跑 web 测试。
