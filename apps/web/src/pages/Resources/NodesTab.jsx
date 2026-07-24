// NodesTab — Clash leaves + catalog subtabs + subscription import-url
import { useCallback, useEffect, useMemo, useState } from "preact/hooks";
import * as api from "../../api/client.js";
import { session } from "../../store/session.js";
import { showOpsFeedback } from "../../store/feedback.js";
import { Button, Select, Tabs, Chip } from "../../ui/index.js";
import { formatApiError, healthBadge } from "../../lib/format.js";

function pretty(v) {
  try {
    return typeof v === "string" ? v : JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function auth(e) {
  if (e && e.status === 401) {
    session.value = { ...session.value, authenticated: false };
    return true;
  }
  return false;
}

function filterClashLeaves(leaves, mode, q) {
  let out = leaves || [];
  if (mode === "pool") out = out.filter((n) => n.in_register_pool);
  else if (mode === "ok") out = out.filter((n) => n.health === "ok");
  else if (mode === "fail") out = out.filter((n) => n.health === "fail");
  const qq = (q || "").trim().toLowerCase();
  if (qq) out = out.filter((n) => (n.name || "").toLowerCase().includes(qq));
  return out.slice().sort((a, b) => {
    const pa = a.in_register_pool ? 0 : 1;
    const pb = b.in_register_pool ? 0 : 1;
    if (pa !== pb) return pa - pb;
    const rank = { ok: 0, unknown: 1, fail: 2 };
    const ha = rank[a.health || "unknown"] ?? 1;
    const hb = rank[b.health || "unknown"] ?? 1;
    if (ha !== hb) return ha - hb;
    const da = a.last_delay_ms != null ? a.last_delay_ms : 1e9;
    const db = b.last_delay_ms != null ? b.last_delay_ms : 1e9;
    return da - db;
  });
}

const CLASH_FILTER = [
  { value: "pool", label: "仅注册池" },
  { value: "all", label: "全部叶子" },
  { value: "ok", label: "仅可用" },
  { value: "fail", label: "仅失败" },
];

// Must match apps.control_api.nodes_ops.IMPORTABLE_GROUPS (register-relevant first).
const IMPORT_POOL_OPTS = [
  { value: "🎯Grok注册", label: "🎯Grok注册 · 注册主池（推荐）" },
  { value: "♻️Grok优选", label: "♻️Grok优选" },
  { value: "PROXY", label: "PROXY" },
  { value: "🔰ChatGPT", label: "🔰ChatGPT" },
  { value: "GROK-REG", label: "GROK-REG · 域名隔离组" },
];

const IMPORT_MODE_OPTS = [
  { value: "merge", label: "merge · 追加/同名覆盖" },
  { value: "replace_prefix", label: "replace_prefix · 替换同前缀" },
];

const HEALTH_OPTS = [
  { value: "", label: "全部" },
  { value: "ok", label: "可用" },
  { value: "fail", label: "失败" },
  { value: "unknown", label: "未测" },
];

const TIER_OPTS = [
  { value: "", label: "全部" },
  { value: "0", label: "机房 0" },
  { value: "1", label: "住宅 1" },
];

const PAGE_SIZES = [
  { value: "25", label: "25" },
  { value: "50", label: "50" },
  { value: "100", label: "100" },
];

export function NodesTab() {
  const [sub, setSub] = useState("clash"); // clash | catalog
  const [clash, setClash] = useState(null);
  const [clashFilter, setClashFilter] = useState("pool");
  const [clashQ, setClashQ] = useState("");
  const [clashResult, setClashResult] = useState("");
  const [busy, setBusy] = useState("");
  const [importUrl, setImportUrl] = useState("");
  // Default real import (write + reload). dry-run is opt-in via checkbox.
  const [importDry, setImportDry] = useState(false);
  const [importGroup, setImportGroup] = useState("🎯Grok注册");
  const [importMode, setImportMode] = useState("merge");
  const [importPrefix, setImportPrefix] = useState("SUB");
  // Pool health: independent from subscription import prefix/mode.
  const [poolTarget, setPoolTarget] = useState("🎯Grok注册");
  const [lastPoolProbe, setLastPoolProbe] = useState(null);

  // catalog state
  const [cat, setCat] = useState(null);
  const [catQ, setCatQ] = useState("");
  const [catHealth, setCatHealth] = useState("");
  const [catTier, setCatTier] = useState("");
  const [catPageSize, setCatPageSize] = useState("50");
  const [catPage, setCatPage] = useState(1);
  const [catResult, setCatResult] = useState("");
  const [addUrl, setAddUrl] = useState("");
  const [addLabel, setAddLabel] = useState("");
  const [addTags, setAddTags] = useState("");
  const [addTier, setAddTier] = useState("0");

  const refreshClash = useCallback(async () => {
    setBusy("clash");
    try {
      const data = await api.listClash();
      setClash(data);
      if (data && !data.ok) setClashResult(pretty(data));
      else setClashResult("");
    } catch (e) {
      if (auth(e)) return;
      setClash(null);
      setClashResult(String(e.message || e));
    } finally {
      setBusy("");
    }
  }, []);

  const refreshCatalog = useCallback(async (override = {}) => {
    setBusy("catalog");
    try {
      const page = override.page != null ? override.page : catPage;
      const q = override.q != null ? override.q : catQ;
      const health = override.health != null ? override.health : catHealth;
      const tier = override.tier != null ? override.tier : catTier;
      const pageSize =
        override.pageSize != null ? override.pageSize : catPageSize;
      const params = new URLSearchParams();
      const qq = String(q || "").trim();
      if (qq) params.set("q", qq);
      if (health) params.set("health", health);
      if (tier !== "") params.set("tier", tier);
      params.set("page", String(page));
      params.set("page_size", pageSize);
      params.set("sort", "priority");
      const data = await api.listCatalog(params.toString());
      setCat(data);
      if (override.page != null) setCatPage(override.page);
      else if (data.page != null) setCatPage(data.page);
    } catch (e) {
      if (auth(e)) return;
      setCat(null);
      setCatResult(String(e.message || e));
    } finally {
      setBusy("");
    }
  }, [catQ, catHealth, catTier, catPage, catPageSize]);

  useEffect(() => {
    if (sub === "clash") refreshClash();
    else refreshCatalog({ page: 1 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sub]);

  const shownLeaves = useMemo(() => {
    if (!clash || !clash.ok) return [];
    return filterClashLeaves(clash.leaves || [], clashFilter, clashQ);
  }, [clash, clashFilter, clashQ]);

  function namesInPool(listing, groupName, limit = 120) {
    const g = (groupName || "").trim();
    const leaves = (listing && listing.leaves) || [];
    let names = leaves
      .filter((x) => Array.isArray(x.groups) && x.groups.includes(g))
      .map((x) => x.name);
    // fallback: register-pool flag when group is a known register group
    if (!names.length && (g.includes("Grok") || g.includes("GROK") || g === "PROXY")) {
      names = leaves.filter((x) => x.in_register_pool).map((x) => x.name);
    }
    return names.slice(0, Math.max(1, limit));
  }

  async function clashTestOne(name) {
    setClashResult("testing…");
    try {
      const r = await api.testClash({ names: [name], timeout_ms: 4000, limit: 1 });
      setClashResult(pretty(r));
      await refreshClash();
    } catch (e) {
      if (auth(e)) return;
      setClashResult(String(e.message || e));
    }
  }

  async function clashTest(limit, poolOnly) {
    setClashResult("testing…");
    setBusy("test");
    try {
      let names = null;
      if (poolOnly) {
        const listing = clash || (await api.listClash());
        names = namesInPool(listing, "🎯Grok注册", limit || 120);
        if (!names.length) {
          names = (listing.leaves || [])
            .filter((x) => x.in_register_pool)
            .map((x) => x.name)
            .slice(0, limit || 120);
        }
      }
      const body = names
        ? { names, limit: names.length || 40, timeout_ms: 4000 }
        : { limit: limit || 40, timeout_ms: 4000 };
      const data = await api.testClash(body);
      setClashResult(pretty(data));
      showOpsFeedback(
        data.ok
          ? `测活完成 · 测 ${data.tested || 0} · 通 ${data.healthy || 0}`
          : `测活失败: ${(data && (data.error || data.message)) || "unknown"}`,
        data.ok ? "ok" : "err",
      );
      await refreshClash();
    } catch (e) {
      if (auth(e)) return;
      setClashResult(String(e.message || e));
      showOpsFeedback(`测活失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  async function doPoolDelayTest() {
    const group = (poolTarget || "").trim() || "🎯Grok注册";
    setBusy("pool-test");
    setClashResult("testing pool…");
    try {
      const listing = clash || (await api.listClash());
      const names = namesInPool(listing, group, 120);
      if (!names.length) {
        showOpsFeedback(`池「${group}」里没有可测叶子节点`, "warn");
        setClashResult(pretty({ ok: false, error: "empty_pool", group }));
        return;
      }
      const data = await api.testClash({
        names,
        limit: names.length,
        timeout_ms: 4000,
      });
      setClashResult(pretty(data));
      const tested = data.tested != null ? data.tested : 0;
      const healthy = data.healthy != null ? data.healthy : 0;
      const dead = Math.max(0, tested - healthy);
      setLastPoolProbe({
        group,
        tested,
        healthy,
        dead,
        ms: data.ms,
        at: Date.now(),
      });
      showOpsFeedback(
        data.ok
          ? `池测活「${group}」· 测 ${tested} · 通 ${healthy} · 不通 ${dead}${data.ms != null ? ` · ${data.ms}ms` : ""}`
          : `池测活失败: ${(data && (data.error || data.message)) || "unknown"}`,
        data.ok ? "ok" : "err",
      );
      await refreshClash();
    } catch (e) {
      if (auth(e)) return;
      setClashResult(String(e.message || e));
      showOpsFeedback(`池测活失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  async function doPoolDeleteDead() {
    const group = (poolTarget || "").trim() || "🎯Grok注册";
    if (
      !window.confirm(
        `确认对「${group}」测活，并删除其中不通的节点？\n\n会从 Clash YAML 去掉不通节点的定义与组引用，然后热重载。\n不影响正在跑的 batch / coinbot 进程。`,
      )
    ) {
      return;
    }
    setBusy("pool-prune");
    try {
      const data = await api.pruneClashUnhealthy({
        prefix: "",
        group,
        dry_run: false,
        reload: true,
        delete_defs: true,
        timeout_ms: 4000,
        limit: 120,
      });
      setClashResult(pretty(data));
      const dead =
        data.unhealthy != null
          ? data.unhealthy
          : data.would_remove != null
            ? data.would_remove
            : 0;
      const tested = data.tested != null ? data.tested : 0;
      const healthy = data.healthy != null ? data.healthy : Math.max(0, tested - dead);
      setLastPoolProbe({
        group,
        tested,
        healthy,
        dead,
        removed: data.removed != null ? data.removed : dead,
        ms: data.ms,
        at: Date.now(),
      });
      showOpsFeedback(
        data.ok
          ? `已删不通「${group}」· 测 ${tested} · 不通 ${dead} · 删除 ${data.removed != null ? data.removed : dead}`
          : `删除不通失败: ${(data && (data.detail || data.error || data.message)) || "unknown"}`,
        data.ok ? "ok" : "err",
      );
      if (data.ok) await refreshClash();
    } catch (e) {
      if (auth(e)) return;
      setClashResult(String(e.message || e));
      showOpsFeedback(`删除不通失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  async function doPoolPreviewDead() {
    const group = (poolTarget || "").trim() || "🎯Grok注册";
    setBusy("pool-preview");
    try {
      const data = await api.pruneClashUnhealthy({
        prefix: "",
        group,
        dry_run: true,
        reload: false,
        delete_defs: true,
        timeout_ms: 4000,
        limit: 120,
      });
      setClashResult(pretty(data));
      const dead =
        data.unhealthy != null
          ? data.unhealthy
          : data.would_remove != null
            ? data.would_remove
            : 0;
      const tested = data.tested != null ? data.tested : 0;
      const healthy = data.healthy != null ? data.healthy : Math.max(0, tested - dead);
      setLastPoolProbe({
        group,
        tested,
        healthy,
        dead,
        preview: true,
        ms: data.ms,
        at: Date.now(),
      });
      showOpsFeedback(
        data.ok
          ? `预检「${group}」· 测 ${tested} · 通 ${healthy} · 将删 ${dead}（未写盘）`
          : `预检失败: ${(data && (data.detail || data.error || data.message)) || "unknown"}`,
        data.ok ? "ok" : "err",
      );
    } catch (e) {
      if (auth(e)) return;
      setClashResult(String(e.message || e));
      showOpsFeedback(`预检失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  async function doImportUrl() {
    const url = importUrl.trim();
    if (!url) {
      showOpsFeedback("请填写订阅 URL", "warn");
      return;
    }
    const group = (importGroup || "").trim() || "🎯Grok注册";
    const prefix = (importPrefix || "").trim() || "SUB";
    const mode = (importMode || "merge").trim() || "merge";
    setBusy("import");
    try {
      // Backend: group + groups — we send both so the chosen pool is explicit
      // (default alone used to look like "whatever is in YAML" when UI omitted it).
      const body = {
        url,
        dry_run: importDry,
        group,
        groups: [group],
        prefix,
        mode,
        reload: !importDry,
      };
      const data = await api.importClashUrl(body);
      setClashResult(pretty(data));
      const ms =
        data.total_ms != null
          ? data.total_ms
          : data.timings && data.timings.total_ms;
      const msTxt = ms != null ? ` · ${Math.round(ms)}ms` : "";
      const poolTxt = (data.groups && data.groups.join(",")) || group;
      const n =
        (data.parse && data.parse.imported) != null
          ? data.parse.imported
          : data.ok
            ? "?"
            : 0;
      showOpsFeedback(
        data.ok
          ? `Clash 导入${importDry ? " dry-run" : ""} · ${n} 节点 → 池 ${poolTxt}${msTxt}`
          : `导入失败: ${(data && (data.detail || data.error || data.message)) || "unknown"}`,
        data.ok ? "ok" : "err",
      );
      if (!importDry && data.ok) await refreshClash();
    } catch (e) {
      if (auth(e)) return;
      setClashResult(String(e.message || e));
      showOpsFeedback(`导入失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  async function onCatalogAction(act, id, enabled) {
    try {
      if (act === "del") {
        if (!window.confirm(`删除节点 ${id}?`)) return;
        setCatResult(pretty(await api.deleteCatalogNode(id)));
      } else if (act === "toggle") {
        setCatResult(
          pretty(await api.patchCatalogNode(id, { enabled: !enabled })),
        );
      } else if (act === "test") {
        setCatResult(pretty(await api.testCatalog({ ids: [id] })));
      }
      await refreshCatalog();
    } catch (e) {
      if (auth(e)) return;
      setCatResult(String(e.message || e));
    }
  }

  async function addNode() {
    try {
      const tags = (addTags || "")
        .split(/[,;]/)
        .map((s) => s.trim())
        .filter(Boolean);
      const data = await api.addCatalogNode({
        url: addUrl.trim(),
        label: addLabel.trim(),
        tags,
        tier: Number(addTier || 0),
        enabled: true,
      });
      setCatResult(pretty(data));
      if (data.ok) {
        setAddUrl("");
        setAddLabel("");
      }
      await refreshCatalog();
    } catch (e) {
      if (auth(e)) return;
      setCatResult(String(e.message || e));
    }
  }

  async function testAllCatalog() {
    setCatResult("testing…");
    try {
      const data = await api.testCatalog({ limit: 50 });
      setCatResult(pretty(data));
      await refreshCatalog();
    } catch (e) {
      if (auth(e)) return;
      setCatResult(String(e.message || e));
    }
  }

  const leaves = clash?.leaves || [];
  const poolN = leaves.filter((x) => x.in_register_pool).length;
  const okN = leaves.filter((x) => x.health === "ok").length;
  const catPages = cat?.pages || 1;
  const catCur = cat?.page || catPage;

  return (
    <div class="resources-tab nodes-tab">
      <div class="card">
        <Tabs
          items={[
            { id: "clash", label: "内置 Clash / mihomo" },
            { id: "catalog", label: "项目 catalog" },
          ]}
          value={sub}
          onChange={setSub}
        />
      </div>

      {sub === "clash" ? (
        <>
          <div class="card actions-bar">
            <Button
              variant="ghost"
              busy={busy === "clash"}
              onClick={refreshClash}
            >
              刷新 Clash
            </Button>
            <Button
              variant="ghost"
              busy={busy === "test"}
              onClick={() => clashTest(40, true)}
            >
              测活注册池
            </Button>
            <Button
              variant="ghost"
              busy={busy === "test"}
              onClick={() => clashTest(40, false)}
            >
              测活（前 40）
            </Button>
            <label class="inline">
              筛选{" "}
              <Select
                value={clashFilter}
                options={CLASH_FILTER}
                onChange={setClashFilter}
              />
            </label>
            <label class="inline">
              搜索{" "}
              <input
                value={clashQ}
                placeholder="名称关键字"
                onInput={(e) => setClashQ(e.currentTarget.value)}
              />
            </label>
            <span class="hint">
              {clash && clash.ok
                ? `api=${clash.api} · leaves=${clash.leaf_count} · 注册池 ${poolN} · 可用 ${okN} · 显示 ${shownLeaves.length} · groups=${clash.group_count} · secret=${clash.secret_configured ? "yes" : "no"}`
                : clash
                  ? `Clash 不可用: ${clash.error || "unknown"} (api=${clash.api || ""})`
                  : "—"}
            </span>
          </div>

          <div class="card">
            <h2>订阅导入</h2>
            <p class="hint" style={{ marginTop: 0 }}>
              节点会写入 Clash YAML 的<strong>指定策略组（池子）</strong>叶子列表。
              注册批跑用的是「🎯Grok注册 / ♻️Grok优选」等注册相关组；不要选无关主策略组。
            </p>
            <div class="actions-bar wrap">
              <input
                class="grow"
                value={importUrl}
                placeholder="https://… subscription URL"
                onInput={(e) => setImportUrl(e.currentTarget.value)}
              />
            </div>
            <div class="actions-bar wrap">
              <label class="inline">
                目标池{" "}
                <Select
                  value={importGroup}
                  options={IMPORT_POOL_OPTS}
                  onChange={setImportGroup}
                />
              </label>
              <label class="inline">
                模式{" "}
                <Select
                  value={importMode}
                  options={IMPORT_MODE_OPTS}
                  onChange={setImportMode}
                />
              </label>
              <label class="inline">
                前缀{" "}
                <input
                  style={{ width: "6rem" }}
                  value={importPrefix}
                  title="节点名前缀，replace_prefix 时按此前缀清理旧订阅"
                  onInput={(e) => setImportPrefix(e.currentTarget.value)}
                />
              </label>
              <label class="check">
                <input
                  type="checkbox"
                  checked={importDry}
                  onChange={(e) => setImportDry(e.currentTarget.checked)}
                />{" "}
                仅预检（不写盘）
              </label>
              <Button
                variant="primary"
                busy={busy === "import"}
                onClick={doImportUrl}
              >
                {importDry ? "预检" : "导入"}
              </Button>
            </div>
            <p class="hint">
              当前目标：<code>{importGroup || "🎯Grok注册"}</code>
              {" · "}
              {importMode}
              {" · "}
              prefix=<code>{importPrefix || "SUB"}</code>
              {importDry ? " · 仅预检不写盘" : " · 导入到所选池并热重载"}
            </p>
          </div>

          <div class="card">
            <h2>池测活 / 删不通</h2>
            <p class="hint" style={{ marginTop: 0 }}>
              选一个策略组（节点池）→ 先<strong>测活</strong>看延迟，或
              <strong>一键删除不通</strong>（delay 失败的节点从 YAML 去掉并热重载）。
              不影响 batch / coinbot 进程。单次最多测/删约 120 个。
            </p>
            <div class="actions-bar wrap">
              <label class="inline">
                节点池{" "}
                <Select
                  value={poolTarget}
                  options={IMPORT_POOL_OPTS}
                  onChange={setPoolTarget}
                />
              </label>
              <Button
                variant="primary"
                busy={busy === "pool-test"}
                onClick={doPoolDelayTest}
              >
                测活该池
              </Button>
              <Button
                variant="ghost"
                busy={busy === "pool-preview"}
                onClick={doPoolPreviewDead}
              >
                预检将删多少
              </Button>
              <Button
                variant="danger"
                busy={busy === "pool-prune"}
                onClick={doPoolDeleteDead}
              >
                删除不通节点
              </Button>
            </div>
            <p class="hint">
              当前池：<code>{poolTarget || "🎯Grok注册"}</code>
              {lastPoolProbe && lastPoolProbe.group === poolTarget
                ? ` · 上次：测 ${lastPoolProbe.tested} · 通 ${lastPoolProbe.healthy} · 不通 ${lastPoolProbe.dead}${
                    lastPoolProbe.preview
                      ? "（预检未写盘）"
                      : lastPoolProbe.removed != null
                        ? ` · 已删 ${lastPoolProbe.removed}`
                        : ""
                  }${lastPoolProbe.ms != null ? ` · ${lastPoolProbe.ms}ms` : ""}`
                : " · 尚未测活"}
            </p>
          </div>

          <div class="card">
            <h2>策略组</h2>
            <div class="chip-row">
              {(clash?.groups || []).map((g, i) => (
                <Chip
                  key={i}
                  class={g.register_relevant ? "hot" : ""}
                  title={`${g.type || ""} now=${g.now || ""}`}
                >
                  {g.name} · {g.count}
                  {g.now ? ` → ${g.now}` : ""}
                </Chip>
              ))}
            </div>
          </div>

          <div class="card table-wrap">
            <table class="data">
              <thead>
                <tr>
                  <th>健康</th>
                  <th>名称</th>
                  <th>类型</th>
                  <th>延迟</th>
                  <th>优先级分</th>
                  <th>注册池</th>
                  <th>所属组</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {!shownLeaves.length ? (
                  <tr>
                    <td colspan="8" class="hint">
                      当前筛选下无节点
                    </td>
                  </tr>
                ) : (
                  shownLeaves.map((n) => {
                    const hb = healthBadge(n.health || "unknown");
                    const groups = (n.groups || []).slice(0, 4).join(", ");
                    return (
                      <tr key={n.name}>
                        <td>
                          <span class={`badge ${hb.cls === "danger" ? "fail" : hb.cls === "ok" ? "ok" : "unknown"}`}>
                            {hb.label}
                          </span>
                        </td>
                        <td>{n.name}</td>
                        <td>{n.type || ""}</td>
                        <td>
                          {n.last_delay_ms != null ? `${n.last_delay_ms}ms` : "—"}
                        </td>
                        <td>
                          {n.priority_score != null ? n.priority_score : "—"}
                        </td>
                        <td>
                          {n.in_register_pool ? (
                            <span class="badge pool">注册池</span>
                          ) : (
                            "—"
                          )}
                        </td>
                        <td class="hint">{groups}</td>
                        <td class="ops">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => clashTestOne(n.name)}
                          >
                            测活
                          </Button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          {clashResult ? <pre class="log compact">{clashResult}</pre> : null}
        </>
      ) : (
        <>
          <div class="card grid">
            <label>
              proxy URL{" "}
              <input
                value={addUrl}
                placeholder="http://user:pass@host:port"
                onInput={(e) => setAddUrl(e.currentTarget.value)}
              />
            </label>
            <label>
              label{" "}
              <input
                value={addLabel}
                onInput={(e) => setAddLabel(e.currentTarget.value)}
              />
            </label>
            <label>
              tags{" "}
              <input
                value={addTags}
                placeholder="residential,us"
                onInput={(e) => setAddTags(e.currentTarget.value)}
              />
            </label>
            <label>
              tier{" "}
              <select
                value={addTier}
                onChange={(e) => setAddTier(e.currentTarget.value)}
              >
                <option value="0">0 datacenter</option>
                <option value="1">1 residential</option>
              </select>
            </label>
            <div class="actions">
              <Button variant="ghost" onClick={addNode}>
                添加节点
              </Button>
              <Button variant="ghost" onClick={testAllCatalog}>
                测活（enabled≤50）
              </Button>
              <Button
                variant="ghost"
                busy={busy === "catalog"}
                onClick={() => refreshCatalog()}
              >
                刷新
              </Button>
            </div>
          </div>

          <div class="card filter-bar">
            <label class="inline">
              搜索{" "}
              <input
                value={catQ}
                onInput={(e) => setCatQ(e.currentTarget.value)}
              />
            </label>
            <label class="inline">
              健康{" "}
              <Select
                value={catHealth}
                options={HEALTH_OPTS}
                onChange={(v) => {
                  setCatHealth(v);
                  refreshCatalog({ page: 1, health: v });
                }}
              />
            </label>
            <label class="inline">
              tier{" "}
              <Select
                value={catTier}
                options={TIER_OPTS}
                onChange={(v) => {
                  setCatTier(v);
                  refreshCatalog({ page: 1, tier: v });
                }}
              />
            </label>
            <label class="inline">
              每页{" "}
              <Select
                value={catPageSize}
                options={PAGE_SIZES}
                onChange={(v) => {
                  setCatPageSize(v);
                  refreshCatalog({ page: 1, pageSize: v });
                }}
              />
            </label>
            <Button
              variant="ghost"
              size="sm"
              disabled={catCur <= 1}
              onClick={() => refreshCatalog({ page: Math.max(1, catCur - 1) })}
            >
              上一页
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={catCur >= catPages}
              onClick={() => refreshCatalog({ page: catCur + 1 })}
            >
              下一页
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refreshCatalog({ page: 1, q: catQ })}
            >
              应用筛选
            </Button>
            <span class="hint">
              page {catCur}/{catPages} · showing {(cat?.nodes || []).length}
            </span>
          </div>

          <div class={`card ${cat && cat.healthy > 0 ? "ok" : "muted"}`}>
            {cat
              ? `path=${cat.path} · total=${cat.total} enabled=${cat.enabled} healthy=${cat.healthy}` +
                (cat.fail != null ? ` fail=${cat.fail}` : "") +
                (cat.unknown != null ? ` unknown=${cat.unknown}` : "") +
                ` · 筛选 ${cat.filtered}/${cat.total} · 第 ${cat.page}/${cat.pages || 1} 页`
              : "—"}
          </div>

          <div class="card table-wrap">
            <table class="data">
              <thead>
                <tr>
                  <th>状态</th>
                  <th>label</th>
                  <th>tier</th>
                  <th>延迟</th>
                  <th>IP</th>
                  <th>优先级</th>
                  <th>失败</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {!(cat?.nodes || []).length ? (
                  <tr>
                    <td colspan="8" class="hint">
                      无匹配节点
                    </td>
                  </tr>
                ) : (
                  (cat.nodes || []).map((n) => {
                    const health =
                      n.health ||
                      (n.last_ok === true
                        ? "ok"
                        : n.last_ok === false
                          ? "fail"
                          : "unknown");
                    const hb = healthBadge(health);
                    return (
                      <tr key={n.id}>
                        <td>
                          <span
                            class={`badge ${hb.cls === "danger" ? "fail" : hb.cls === "ok" ? "ok" : "unknown"}`}
                          >
                            {hb.label}
                          </span>
                          {n.cooling
                            ? ` · cool:${n.cooldown_reason || ""}`
                            : ""}
                          {n.enabled === false ? (
                            <span class="badge fail">禁用</span>
                          ) : null}
                        </td>
                        <td>
                          <div>{n.label || n.id}</div>
                          <div class="hint">{n.id}</div>
                        </td>
                        <td>{n.tier === 1 ? "住宅" : "机房"}</td>
                        <td>{n.last_ms != null ? `${n.last_ms}ms` : "—"}</td>
                        <td>{n.last_ip || "—"}</td>
                        <td>
                          {n.priority_score != null
                            ? n.priority_score
                            : n.quality_score != null
                              ? n.quality_score
                              : "—"}
                        </td>
                        <td>
                          {n.fail_count || 0}
                          {n.last_error ? (
                            <div class="hint">{n.last_error}</div>
                          ) : null}
                        </td>
                        <td class="ops">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onCatalogAction("test", n.id)}
                          >
                            测活
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() =>
                              onCatalogAction("toggle", n.id, n.enabled !== false)
                            }
                          >
                            {n.enabled === false ? "启用" : "禁用"}
                          </Button>
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() => onCatalogAction("del", n.id)}
                          >
                            删除
                          </Button>
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
          {catResult ? <pre class="log compact">{catResult}</pre> : null}
        </>
      )}
    </div>
  );
}
