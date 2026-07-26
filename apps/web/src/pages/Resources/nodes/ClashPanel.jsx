// ClashPanel — 内置 Clash / mihomo：订阅导入 + 池测活 + 策略组 + 叶子表。
// Moved verbatim from NodesTab.jsx — JSX/classes/copy unchanged.
import { useCallback, useEffect, useMemo, useState } from "preact/hooks";
import * as api from "../../../api/client.js";
import { showOpsFeedback } from "../../../store/feedback.js";
import { Button, Select, Chip, LogDetails } from "../../../ui/index.js";
import { formatApiError, healthBadge } from "../../../lib/format.js";
import { pretty, auth401 } from "../../../lib/http.js";
import { useBusy } from "../../../lib/useBusy.js";
import {
  filterClashLeaves,
  namesInPool,
  CLASH_FILTER,
  IMPORT_POOL_OPTS,
  IMPORT_MODE_OPTS,
} from "./clashFilters.js";

export function ClashPanel({ active = true }) {
  const [clash, setClash] = useState(null);
  const [clashFilter, setClashFilter] = useState("pool");
  const [clashQ, setClashQ] = useState("");
  const [clashResult, setClashResult] = useState("");
  const { setBusy, is: busyIs } = useBusy();
  const [importUrl, setImportUrl] = useState("");
  // Default real import (write + reload). dry-run is opt-in via checkbox.
  const [importDry, setImportDry] = useState(false);
  const [importGroup, setImportGroup] = useState("🎯Grok注册");
  const [importMode, setImportMode] = useState("merge");
  const [importPrefix, setImportPrefix] = useState("SUB");
  // Pool health: independent from subscription import prefix/mode.
  const [poolTarget, setPoolTarget] = useState("🎯Grok注册");
  const [lastPoolProbe, setLastPoolProbe] = useState(null);

  const refreshClash = useCallback(async () => {
    setBusy("clash");
    try {
      const data = await api.listClash();
      setClash(data);
      if (data && !data.ok) setClashResult(pretty(data));
      else setClashResult("");
    } catch (e) {
      if (auth401(e)) return;
      setClash(null);
      setClashResult(String(e.message || e));
    } finally {
      setBusy("");
    }
  }, []);

  const shownLeaves = useMemo(() => {
    if (!clash || !clash.ok) return [];
    return filterClashLeaves(clash.leaves || [], clashFilter, clashQ);
  }, [clash, clashFilter, clashQ]);

  async function clashTestOne(name) {
    setClashResult("testing…");
    try {
      const r = await api.testClash({ names: [name], timeout_ms: 4000, limit: 1 });
      setClashResult(pretty(r));
      await refreshClash();
    } catch (e) {
      if (auth401(e)) return;
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
      if (auth401(e)) return;
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
      if (auth401(e)) return;
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
      if (auth401(e)) return;
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
      if (auth401(e)) return;
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
      if (auth401(e)) return;
      setClashResult(String(e.message || e));
      showOpsFeedback(`导入失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }


  const leaves = clash?.leaves || [];
  const poolN = leaves.filter((x) => x.in_register_pool).length;
  const okN = leaves.filter((x) => x.health === "ok").length;

  // Same semantics as the old combined NodesTab: refresh on every switch to
  // this subtab (the original effect keyed on `sub`), skip while inactive.
  useEffect(() => {
    if (active) refreshClash();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  if (!active) return null;

  return (
    <>
      <div class="card actions-bar">
        <Button
          variant="ghost"
          busy={busyIs("clash")}
          onClick={refreshClash}
        >
          刷新 Clash
        </Button>
        <Button
          variant="ghost"
          busy={busyIs("test")}
          onClick={() => clashTest(40, true)}
        >
          测活注册池
        </Button>
        <Button
          variant="ghost"
          busy={busyIs("test")}
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
            busy={busyIs("import")}
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
            busy={busyIs("pool-test")}
            onClick={doPoolDelayTest}
          >
            测活该池
          </Button>
          <Button
            variant="ghost"
            busy={busyIs("pool-preview")}
            onClick={doPoolPreviewDead}
          >
            预检将删多少
          </Button>
          <Button
            variant="danger"
            busy={busyIs("pool-prune")}
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
      <LogDetails text={clashResult} summary="Clash 响应详情" compact />
    </>
  );
}
