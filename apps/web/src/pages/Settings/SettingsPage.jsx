// Settings — pipeline email providers + proxy/turnstile/cpa + bearer + ops.
// Per-provider secrets/domains live only on Resources → 邮箱.
import { useEffect, useState } from "preact/hooks";
import * as api from "../../api/client.js";
import { getToken, setToken } from "../../api/client.js";
import { showOpsFeedback } from "../../store/feedback.js";
import { Button, PageHeader, LogDetails, ProviderPoolStatus } from "../../ui/index.js";
import { formatApiError } from "../../lib/format.js";
import { pretty, auth401 } from "../../lib/http.js";
import { useConfigForm } from "../../lib/useConfigForm.js";
import {
  EMAIL_PROVIDERS,
  EMAIL_PROVIDER_STRATEGIES,
  normalizeProvidersList,
} from "../../lib/providers.js";
import "../../styles/settings.css";

const EMPTY = {
  email_providers: [],
  email_provider_strategy: "round_robin",
  proxy: "",
  proxy_rotate_mode: "",
  proxy_list: "",
  turnstile_stuck_timeout: "",
  cpa_probe_chat: "",
  cpa_remote_inject: "",
};

export function SettingsPage() {
  // Residual singleton primary (not editable here). Shown when multi is empty.
  const [primaryProvider, setPrimaryProvider] = useState("");
  const [token, setTokenLocal] = useState(getToken());
  const [result, setResult] = useState("");

  const cfg = useConfigForm({
    empty: EMPTY,
    // Snapshot of last successful load — used to detect intentional clearable wipes.
    initialBaseline: { email_providers: [], proxy_list: "" },
    loadRequest: async () => {
      const data = await api.getConfig();
      return data.config || {};
    },
    hydrate: (c) => {
      const next = { ...EMPTY };
      // Multi-list only — do NOT fall back to singleton email_provider.
      // Empty multi means intentional single-channel mode; reloading must keep
      // chips empty so a clear+reload+save cannot re-open the pool.
      next.email_providers = normalizeProvidersList(c.email_providers);
      const strat = String(c.email_provider_strategy || "round_robin").trim().toLowerCase();
      next.email_provider_strategy = EMAIL_PROVIDER_STRATEGIES.includes(strat)
        ? strat
        : "round_robin";
      for (const k of Object.keys(EMPTY)) {
        if (k === "email_providers" || k === "email_provider_strategy") continue;
        if (c[k] == null) continue;
        if (k === "cpa_probe_chat" || k === "cpa_remote_inject") {
          if (c[k] === true) next[k] = "true";
          else if (c[k] === false) next[k] = "false";
          else next[k] = String(c[k]);
        } else if (k === "proxy_list") {
          next[k] = Array.isArray(c[k]) ? c[k].join("\n") : String(c[k]);
        } else {
          next[k] = String(c[k]);
        }
      }
      return {
        form: next,
        baseline: {
          email_providers: next.email_providers.slice(),
          proxy_list: String(next.proxy_list || ""),
        },
      };
    },
    onLoaded: (c, next) => {
      const primary = String(c.email_provider || "").trim().toLowerCase();
      setPrimaryProvider(primary);
      setResult(
        pretty({
          loaded: true,
          email_providers: next.email_providers,
          email_provider: primary || null,
          email_provider_strategy: next.email_provider_strategy,
        }),
      );
    },
    onLoadError: (e) => {
      setResult(String(e.message || e));
      showOpsFeedback(`加载设置失败: ${formatApiError(e)}`, "err");
    },
    confirmReload: {
      message: "有未保存的设置更改，确认丢弃并重新加载？",
      onCancel: () =>
        showOpsFeedback("已取消 Reload", "info", { toast: true, sticky: false }),
    },
    onSaveBlocked: () =>
      showOpsFeedback("请先加载配置后再保存（避免空表单清空多选池）", "err", {
        toast: true,
        sticky: true,
      }),
    confirmClear: (f, baseline) => {
      const providers = normalizeProvidersList(f.email_providers);
      const proxyListNow = String(f.proxy_list || "");
      const clearing = [];
      if (!providers.length && baseline.email_providers.length) {
        clearing.push("email_providers（多选池）");
      }
      if (!proxyListNow.trim() && String(baseline.proxy_list || "").trim()) {
        clearing.push("proxy_list / PROXY_LIST");
      }
      if (!clearing.length) return null;
      return {
        message: `确认清空以下 clearable 字段？\n\n· ${clearing.join("\n· ")}\n\n将写入空值到 config 与 .env。`,
      };
    },
    onSaveCancelled: () =>
      showOpsFeedback("已取消保存", "info", { toast: true, sticky: false }),
    saveRequest: (f) => {
      const providers = normalizeProvidersList(f.email_providers);
      // Settings is allowed to clear the multi-select pool (→ single email_provider).
      const partial = {
        email_providers: providers,
        email_provider_strategy: f.email_provider_strategy || "round_robin",
      };
      if (providers.length) {
        partial.email_provider = providers[0];
      }
      for (const [k, raw] of Object.entries(f)) {
        if (k === "email_providers" || k === "email_provider_strategy") continue;
        let v = raw;
        if (k === "cpa_probe_chat" || k === "cpa_remote_inject") {
          if (v === "") continue;
          if (v === "true") v = true;
          else if (v === "false") v = false;
          partial[k] = v;
          continue;
        }
        if (k === "turnstile_stuck_timeout") {
          if (v === "") continue;
          partial[k] = Number(v);
          continue;
        }
        if (v === "" && k !== "proxy_list") continue;
        partial[k] = v;
      }
      return api.putConfig({ config: partial });
    },
    onSaved: (data, f) => {
      const providers = normalizeProvidersList(f.email_providers);
      const proxyListNow = String(f.proxy_list || "");
      const strategy = f.email_provider_strategy || "round_robin";
      setResult(pretty(data));
      const saved = data.config || {};
      const savedPrimary = String(saved.email_provider || primaryProvider || "").trim();
      if (savedPrimary) setPrimaryProvider(savedPrimary.toLowerCase());
      const savedMulti = Array.isArray(saved.email_providers)
        ? normalizeProvidersList(saved.email_providers)
        : providers;
      const savedProxyList =
        saved.proxy_list != null
          ? Array.isArray(saved.proxy_list)
            ? saved.proxy_list.join("\n")
            : String(saved.proxy_list)
          : proxyListNow;
      showOpsFeedback(
        providers.length
          ? `设置已保存 · providers=${providers.join(",")} · strategy=${strategy}`
          : `设置已保存 · multi=∅ · 单通道=${savedPrimary || primaryProvider || "—"} · strategy=${strategy}`,
        "ok",
      );
      return {
        form: (p) => ({
          ...p,
          // Prefer server multi-list; empty server list after clear stays empty
          // (do not fall back to singleton — Settings owns clearable pool).
          email_providers: savedMulti,
          email_provider_strategy:
            String(saved.email_provider_strategy || p.email_provider_strategy || "round_robin")
              .trim()
              .toLowerCase() || "round_robin",
          proxy_list: savedProxyList,
        }),
        baseline: {
          email_providers: savedMulti.slice(),
          proxy_list: savedProxyList,
        },
      };
    },
    onSaveError: (e) => {
      setResult(String(e.message || e));
      showOpsFeedback(`保存失败: ${formatApiError(e)}`, "err");
    },
  });
  const { form, set, hydrated, dirty, load, busyIs, setBusy } = cfg;

  useEffect(() => {
    load();
    // initial mount only — load identity changes with dirty; do not re-fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleProvider(name) {
    const cur = new Set(form.email_providers || []);
    if (cur.has(name)) cur.delete(name);
    else cur.add(name);
    set({ email_providers: EMAIL_PROVIDERS.filter((p) => cur.has(p)) });
  }

  function save(ev) {
    if (ev) ev.preventDefault();
    return cfg.save();
  }

  function saveBearer() {
    setToken(token.trim());
    showOpsFeedback(
      token.trim() ? "Bearer 已写入 sessionStorage" : "Bearer 已清除",
      "ok",
      { toast: true, sticky: false },
    );
  }

  async function runSelfcheck() {
    setBusy("selfcheck");
    try {
      const data = await api.selfcheck();
      setResult(pretty(data));
      showOpsFeedback("自检完成", "ok");
    } catch (e) {
      if (auth401(e)) return;
      setResult(String(e.message || e));
      showOpsFeedback(`自检失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  async function runCleanup() {
    if (
      !window.confirm(
        "确认清理过盾残留？\n\n会对本机 orphan chromium / 残留目录做清理（dry_run=false）。",
      )
    ) {
      showOpsFeedback("已取消清理", "info", { toast: true, sticky: false });
      return;
    }
    setBusy("cleanup");
    try {
      const data = await api.cleanupOrphans();
      setResult(pretty(data));
      showOpsFeedback("清理完成", "ok");
    } catch (e) {
      if (auth401(e)) return;
      setResult(String(e.message || e));
      showOpsFeedback(`清理失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  const selected = form.email_providers || [];

  return (
    <section class="page page-settings">
      <PageHeader
        title="设置"
        hint={
          <>
            这里配置注册链路用哪些邮箱 Provider 与轮询策略。与注册页共用
            <code> email_providers </code>
            （两处保存会互相覆盖）。各 Provider 的密钥 / 域名 / 凭证请到
            <a href="#/resources">资源 → 邮箱</a>
            细配。
          </>
        }
      >
        <Button
          variant="ghost"
          busy={busyIs("load")}
          onClick={() => load({ force: true })}
        >
          Reload
        </Button>
        <Button
          variant="primary"
          busy={busyIs("save")}
          disabled={!hydrated || busyIs("load")}
          onClick={save}
          title={!hydrated ? "请先加载配置" : undefined}
        >
          Save
        </Button>
      </PageHeader>

      <form class="card grid settings-form" onSubmit={save}>
        <div class="span2 provider-pipeline">
          <div class="settings-section-title">邮箱链路（可用 Provider）</div>
          <ProviderPoolStatus
            selected={selected}
            primaryProvider={primaryProvider}
            canClear
            hydrated={hydrated}
            dirty={dirty}
          />
          <p class="hint tight">
            勾选写入 <code>email_providers</code>；清空勾选即清空池，退回单通道{" "}
            <code>email_provider</code>。细配 → <a href="#/resources?tab=mail">资源 · 邮箱</a>
          </p>
          <div class="mail-domain-chips provider-multi">
            {EMAIL_PROVIDERS.map((p) => (
              <label key={p} class="check chip">
                <input
                  type="checkbox"
                  checked={selected.includes(p)}
                  onChange={() => toggleProvider(p)}
                />{" "}
                {p}
              </label>
            ))}
          </div>
        </div>
        <label class="span2">
          轮询策略（email_provider_strategy）
          <select
            value={form.email_provider_strategy}
            onChange={(e) => set({ email_provider_strategy: e.currentTarget.value })}
          >
            {EMAIL_PROVIDER_STRATEGIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>

        <div class="span2 settings-section-title">代理</div>
        <label>
          单条代理（proxy）
          <input
            value={form.proxy}
            onInput={(e) => set({ proxy: e.currentTarget.value })}
          />
        </label>
        <label>
          轮换模式（proxy_rotate_mode）
          <input
            value={form.proxy_rotate_mode}
            onInput={(e) => set({ proxy_rotate_mode: e.currentTarget.value })}
          />
        </label>
        <label class="span2">
          代理列表（proxy_list · 每行一条）
          <textarea
            rows={3}
            value={form.proxy_list}
            onInput={(e) => set({ proxy_list: e.currentTarget.value })}
          />
        </label>

        <div class="span2 settings-section-title">注册行为</div>
        <label>
          Turnstile 卡死超时秒（turnstile_stuck_timeout）
          <input
            type="number"
            step="1"
            value={form.turnstile_stuck_timeout}
            onInput={(e) => set({ turnstile_stuck_timeout: e.currentTarget.value })}
          />
        </label>
        <label>
          注册后探测 chat（cpa_probe_chat）
          <select
            value={form.cpa_probe_chat}
            onChange={(e) => set({ cpa_probe_chat: e.currentTarget.value })}
          >
            <option value="">(unchanged)</option>
            <option value="false">false</option>
            <option value="true">true</option>
          </select>
        </label>
        <label>
          远端注入意向（cpa_remote_inject）
          <select
            value={form.cpa_remote_inject}
            onChange={(e) => set({ cpa_remote_inject: e.currentTarget.value })}
          >
            <option value="">(unchanged)</option>
            <option value="false">false</option>
            <option value="true">true</option>
          </select>
        </label>
      </form>

      <div class="card">
        <h2>脚本 Bearer token（可选）</h2>
        <label class="inline">
          API Token{" "}
          <input
            type="password"
            value={token}
            placeholder="optional bearer"
            autocomplete="off"
            onInput={(e) => setTokenLocal(e.currentTarget.value)}
          />
        </label>
        <Button variant="ghost" onClick={saveBearer}>
          Save token
        </Button>
      </div>

      <div class="card">
        <h2>运维自检</h2>
        <div class="toolbar wrap">
          <Button variant="ghost" busy={busyIs("selfcheck")} onClick={runSelfcheck}>
            运行自检
          </Button>
          <Button variant="ghost" busy={busyIs("cleanup")} onClick={runCleanup}>
            清理过盾残留
          </Button>
        </div>
        <LogDetails text={result} summary="自检 / 清理响应详情" />
      </div>
    </section>
  );
}
