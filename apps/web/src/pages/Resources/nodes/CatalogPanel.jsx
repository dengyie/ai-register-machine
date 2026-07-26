// CatalogPanel — 项目 catalog：添加节点 + 筛选分页 + catalog 表。
// Moved verbatim from NodesTab.jsx — JSX/classes/copy unchanged.
import { useCallback, useEffect, useState } from "preact/hooks";
import * as api from "../../../api/client.js";
import { showOpsFeedback } from "../../../store/feedback.js";
import { Button, Select, LogDetails } from "../../../ui/index.js";
import { formatApiError, healthBadge } from "../../../lib/format.js";
import { pretty, auth401 } from "../../../lib/http.js";
import { useBusy } from "../../../lib/useBusy.js";
import { HEALTH_OPTS, TIER_OPTS, PAGE_SIZES } from "./clashFilters.js";

export function CatalogPanel({ active = true }) {
  const { setBusy, is: busyIs } = useBusy();
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
      if (auth401(e)) return;
      setCat(null);
      setCatResult(String(e.message || e));
    } finally {
      setBusy("");
    }
  }, [catQ, catHealth, catTier, catPage, catPageSize]);

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
      if (auth401(e)) return;
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
      if (auth401(e)) return;
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
      if (auth401(e)) return;
      setCatResult(String(e.message || e));
    }
  }

  const catPages = cat?.pages || 1;
  const catCur = cat?.page || catPage;

  // Same semantics as the old combined NodesTab: refresh on every switch to
  // this subtab (the original effect keyed on `sub`), skip while inactive.
  useEffect(() => {
    if (active) refreshCatalog({ page: 1 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  if (!active) return null;

  return (
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
            busy={busyIs("catalog")}
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
      <LogDetails text={catResult} summary="catalog 响应详情" compact />
    </>
  );
}
