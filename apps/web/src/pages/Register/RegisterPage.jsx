// src/pages/Register/RegisterPage.jsx
// Register / control home: left form + right progress. Full parity with legacy
// register UX (apps/web/legacy/assets/app.js register + renderRunStatus pipeline).
//
// Key behaviors:
//  - 4s poll of api.currentRun() + api.overview(); never wipes form while
//    regFormDirty.value === true; form loaded from config only once.
//  - startRun body keys mirror legacy (kind/product/mode/target/threads/
//    tag/extra_env{SUPERVISOR_CHUNK,CPA_BATCH_END_INJECT,CPA_BATCH_IMPORT_*,
//    CPA_PROBE_CHAT=false,SKIP_CLASH_PREFLIGHT,NODE_SCORE,EMAIL_PROVIDERS,
//    EMAIL_PROVIDER}). Secrets/domains are NOT written from this page.
//  - stopRun: window.confirm Chinese warning first.
//  - putConfig wrapped as { config: partial } (backend ConfigPutIn schema).
//  - 401 → session.authenticated=false (gate shows).
//  - 接口/运维: 保存 / 自检(link #/settings) / 测代理. No duplicate cleanup/selfcheck
//    buttons here (spec IA — selfcheck lives on settings).
import { useEffect, useState, useCallback } from "preact/hooks";
import * as api from "../../api/client.js";
import { session } from "../../store/session.js";
import { showOpsFeedback } from "../../store/feedback.js";
import {
  currentRunState,
  overviewState,
  regFormDirty,
  regFormLoaded,
  lastProductOk,
} from "../../store/run.js";
import { RegForm } from "./RegForm.jsx";
import { RunProgress } from "./RunProgress.jsx";
import { formatApiError as formatApiErrorShared } from "../../lib/format.js";
import { normalizeProvidersList } from "../../lib/providers.js";
import "../../styles/run.css";

// Initial form state (also used before config loads). Mirrors legacy defaults.
// Email: multi-select pool only — secrets/domains on Resources, strategy on Settings.
// email_providers starts empty until GET /api/config (no singleton pre-fill).
const initialForm = {
  email_providers: [],
  target: 100,
  threads: 1,
  mode: "ordinary",
  tag: "batch_web",
  chunk: 3,
  turnstile: 150,
  ssoOnly: true,
  batchEndInject: false,
  importEvery: 100,
  importSize: "",
  importPause: "",
  proxyMode: "clash",
  proxy: "",
  proxyList: "",
  kind: "grok_supervisor",
  product: "grok",
  skipPreflight: false,
  nodeScore: "",
  syncMailEnv: true,
};

function formatApiError(e) {
  if (!e) return "未知错误";
  if (e.status === 409) {
    const msg = String(e.message || e);
    return `已有任务在跑（${msg}）。首页会显示当前 progress；如需停请用「停止」（会杀外部 supervisor）。`;
  }
  if (e.status === 401) return "未登录或会话过期，请重新登录。";
  if (e.status === 422) return `参数校验失败: ${formatApiErrorShared(e)}`;
  return formatApiErrorShared(e);
}

function snapshotFormFromConfig(c, prev) {
  // No prev → first load: apply defaults, then overlay config protocol bits
  // (email pool / proxy / turnstile). Run params stay at HTML defaults.
  // prev → forced refresh: re-hydrate protocol bits but PRESERVE user edits to
  // run params.
  // Multi-list only — never promote singleton email_provider into chips
  // (Settings clear-pool must survive Register load/save).
  const f = { ...initialForm, ...(prev || {}) };
  f.email_providers = normalizeProvidersList(c.email_providers);
  if (c.proxy != null) f.proxy = String(c.proxy);
  if (c.proxy_rotate_mode) f.proxyMode = c.proxy_rotate_mode;
  if (c.proxy_list != null) {
    const pl = c.proxy_list;
    f.proxyList = Array.isArray(pl) ? pl.join("\n") : String(pl);
  }
  if (c.turnstile_stuck_timeout != null) {
    f.turnstile = Number(c.turnstile_stuck_timeout);
  }
  f.probeChat = false; // supervisor hard-forced off
  return f;
}

export function RegisterPage() {
  const [form, setForm] = useState(initialForm);
  // Residual singleton when multi is empty (Settings single-channel mode).
  const [primaryProvider, setPrimaryProvider] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [busyKey, setBusyKey] = useState(null); // 'start' | 'stop' | 'save' | 'proxy' | 'refresh'
  const [actionResult, setActionResult] = useState("");

  // Load form from /api/config exactly once (and on explicit refresh).
  const loadForm = useCallback(async ({ force = false } = {}) => {
    if (regFormLoaded.value && !force) return;
    if (!force && regFormDirty.value && regFormLoaded.value) return;
    try {
      const data = await api.getConfig();
      const c = data.config || {};
      setPrimaryProvider(String(c.email_provider || "").trim().toLowerCase());
      setForm((prev) =>
        snapshotFormFromConfig(c, force ? prev : undefined),
      );
      regFormLoaded.value = true;
      if (force) regFormDirty.value = false;
    } catch (e) {
      if (e.status === 401) {
        session.value = { ...session.value, authenticated: false };
        return;
      }
      if (force) showOpsFeedback(`加载配置失败: ${formatApiError(e)}`, "err");
    }
  }, []);

  // 4s poll: run + overview only. Never destroys form edits.
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const [cur, ov] = await Promise.all([api.currentRun(), api.overview()]);
        if (cancelled) return;
        const run = cur.run ?? cur ?? null;
        currentRunState.value = run;
        if (ov) {
          overviewState.value = ov;
          if (ov.product_ok != null) lastProductOk.value = ov.product_ok;
        }
      } catch (e) {
        if (e.status === 401) {
          session.value = { ...session.value, authenticated: false };
        }
        // poll failures stay silent (no toast spam)
      }
    }
    loadForm({ force: false });
    tick();
    const id = setInterval(tick, 4000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function gotoLogs() {
    location.hash = "#/logs";
  }

  // Build the start body exactly mirroring legacy startRun().
  function buildStartBody() {
    const v = form;
    const kind = v.kind || "grok_supervisor";
    const product =
      kind === "grok_supervisor" ? "grok" : v.product || "grok";
    const extra_env = {};

    if (kind === "grok_supervisor") {
      const chunk = String(v.chunk ?? "").trim();
      if (chunk) extra_env.SUPERVISOR_CHUNK = chunk;
      extra_env.CPA_BATCH_END_INJECT = v.batchEndInject ? "true" : "false";
      const every = String(v.importEvery ?? "").trim();
      if (every) extra_env.CPA_BATCH_IMPORT_EVERY = every;
      const size = String(v.importSize ?? "").trim();
      if (size) extra_env.CPA_BATCH_IMPORT_SIZE = size;
      const pause = String(v.importPause ?? "").trim();
      if (pause !== "") extra_env.CPA_BATCH_IMPORT_PAUSE = pause;
    }

    // Universal: probe-off
    extra_env.CPA_PROBE_CHAT = "false";

    if (v.skipPreflight) extra_env.SKIP_CLASH_PREFLIGHT = "1";
    const nodeScore = String(v.nodeScore ?? "").trim();
    if (nodeScore !== "") extra_env.NODE_SCORE = nodeScore;

    if (v.syncMailEnv) {
      const pool = normalizeProvidersList(v.email_providers);
      if (pool.length) {
        extra_env.EMAIL_PROVIDERS = pool.join(",");
        // Keep single EMAIL_PROVIDER aligned with first pool member for
        // code paths that still read the singleton.
        extra_env.EMAIL_PROVIDER = pool[0];
      }
      // Empty multi: do NOT inject EMAIL_PROVIDERS (would not clear disk pool
      // via empty env anyway). Start is blocked unless chips are checked.
    }

    return {
      kind,
      product,
      mode: v.mode || "ordinary",
      target: Number(v.target == null || v.target === "" ? 100 : v.target),
      threads: Number(v.threads == null || v.threads === "" ? 1 : v.threads),
      tag: String(v.tag || "batch_web").trim() || "batch_web",
      extra_env,
    };
  }

  // Build the config partial from the form (save path).
  // Register only writes email_providers + proxy/turnstile knobs —
  // never secrets or defaultDomains (those live on Resources).
  // Empty provider multi-select is OMITTED (not clearable from this page) so a
  // mis-click cannot wipe the saved EMAIL_PROVIDERS pool.
  // Empty proxy_list is also OMITTED — intentional clear lives on Settings.
  function buildConfigPartial() {
    const v = form;
    const providers = normalizeProvidersList(v.email_providers);
    const proxy = (v.proxy || "").trim();
    const proxyList = String(v.proxyList ?? "").trim();
    const partial = {
      proxy_rotate_mode: v.proxyMode,
      turnstile_stuck_timeout: Number(v.turnstile || 150),
      // disk-first mid-mint inject always off; batch-end inject is CPA_BATCH_END_INJECT (extra_env).
      cpa_remote_inject: false,
      cpa_probe_chat: false,
    };
    if (providers.length) {
      partial.email_providers = providers;
      // Primary for UIs / code paths that still read the singleton.
      partial.email_provider = providers[0];
    }
    if (proxy) partial.proxy = proxy;
    // Non-empty only: blank must not clear PROXY_LIST via clearable write-through.
    if (proxyList) partial.proxy_list = v.proxyList;
    return { partial, providers };
  }

  async function saveConfig({ silent = false } = {}) {
    // Never persist the pre-load default form (would risk proxy_list/provider drift).
    if (!regFormLoaded.value) {
      if (!silent) {
        showOpsFeedback("请先加载配置后再保存", "err", { toast: true, sticky: true });
      }
      throw new Error("config not loaded");
    }
    if (!silent) setBusyKey("save");
    try {
      const { partial, providers } = buildConfigPartial();
      // Wrap in { config: partial } — backend ConfigPutIn schema.
      const data = await api.putConfig({ config: partial });
      regFormDirty.value = false;
      regFormLoaded.value = true;
      // Re-hydrate from server multi only (never singleton → chips).
      const saved = data.config || {};
      if (saved.email_provider != null) {
        setPrimaryProvider(String(saved.email_provider || "").trim().toLowerCase());
      }
      const serverMulti = normalizeProvidersList(saved.email_providers);
      setForm((p) => {
        const next = {
          ...p,
          // Prefer server multi. Empty form omit leaves disk multi intact →
          // show server list so UI matches disk (Register cannot clear pool).
          email_providers: serverMulti.length
            ? serverMulti
            : providers.length
              ? providers
              : [],
        };
        if (saved.proxy != null && String(saved.proxy).trim()) {
          next.proxy = String(saved.proxy);
        }
        return next;
      });
      if (!silent) {
        const envN = (data.changed_env_keys || []).length;
        const envHint = envN ? ` · env×${envN}` : "";
        if (!providers.length) {
          const kept = serverMulti.join(",") || "∅";
          showOpsFeedback(
            `配置已保存 · 空勾选未改池 · 服务端 providers=${kept}${envHint}`,
            "ok",
          );
        } else {
          showOpsFeedback(
            `配置已保存 · providers=${providers.join(",")}${envHint}`,
            "ok",
          );
        }
      }
      return data;
    } catch (e) {
      if (e.status === 401) {
        session.value = { ...session.value, authenticated: false };
        throw e;
      }
      if (!silent) showOpsFeedback(`保存失败: ${formatApiError(e)}`, "err");
      throw e;
    } finally {
      if (!silent) setBusyKey(null);
    }
  }

  async function refresh() {
    setBusyKey("refresh");
    try {
      await loadForm({ force: true });
      // One immediate run refresh too.
      const cur = await api.currentRun();
      const run = cur.run ?? cur ?? null;
      currentRunState.value = run;
      try {
        const ov = await api.overview();
        overviewState.value = ov;
        if (ov && ov.product_ok != null) lastProductOk.value = ov.product_ok;
      } catch {
        /* render with last known */
      }
      const phase = (run && (run.phase_title || run.phase)) || "—";
      const alive = !!(run && run.alive);
      const sub = run && run.sub != null ? ` sub=${run.sub}` : "";
      const complete = run && run.complete != null ? ` complete=${run.complete}` : "";
      const worker =
        run && run.worker_log
          ? ` · worker=${String(run.worker_log).split(/[\\/]/).pop()}`
          : "";
      showOpsFeedback(
        `已刷新 · ${alive ? "运行中" : "空闲"} · ${phase}${sub}${complete}${worker}`,
        "ok",
        { toast: true, sticky: true },
      );
    } catch (e) {
      if (e.status === 401) {
        session.value = { ...session.value, authenticated: false };
        return;
      }
      showOpsFeedback(formatApiError(e), "err");
    } finally {
      setBusyKey(null);
    }
  }

  async function start() {
    if (!regFormLoaded.value) {
      showOpsFeedback("请先等待配置加载完成再启动", "err", {
        toast: true,
        sticky: true,
      });
      return;
    }
    const providers = normalizeProvidersList(form.email_providers);
    if (!providers.length) {
      showOpsFeedback(
        primaryProvider
          ? `请勾选至少一个 Provider 再启动（当前单通道 ${primaryProvider} 仅在设置清空 multi 后由 runtime 使用；注册页不会把 singleton 写回池）`
          : "请至少勾选一个邮箱 Provider 再启动（空勾选不会清空已保存池，本批也不注入 EMAIL_PROVIDERS）",
        "err",
        { toast: true, sticky: true },
      );
      return;
    }
    setBusyKey("start");
    showOpsFeedback("正在保存配置并启动…", "info", { toast: false, sticky: true });
    try {
      await saveConfig({ silent: true });
    } catch (e) {
      showOpsFeedback(
        `配置保存失败（仍尝试启动）: ${formatApiError(e)}`,
        "warn",
        { toast: true, sticky: true },
      );
    }
    const body = buildStartBody();
    try {
      const data = await api.startRun(body);
      const pid = data && data.run && data.run.pid;
      const detail = (data && data.detail) || "started";
      showOpsFeedback(
        `已启动 · ${detail}${pid != null ? ` pid=${pid}` : ""} · tag=${body.tag}`,
        "ok",
      );
      // immediate progress refresh
      try {
        const cur = await api.currentRun();
        currentRunState.value = cur.run ?? cur ?? null;
        const ov = await api.overview();
        overviewState.value = ov;
        if (ov && ov.product_ok != null) lastProductOk.value = ov.product_ok;
      } catch {
        /* fine */
      }
    } catch (e) {
      if (e.status === 401) {
        session.value = { ...session.value, authenticated: false };
        return;
      }
      showOpsFeedback(formatApiError(e), "err");
      if (e.status === 409) {
        try {
          const cur = await api.currentRun();
          currentRunState.value = cur.run ?? cur ?? null;
        } catch {
          /* ignore */
        }
      }
    } finally {
      setBusyKey(null);
    }
  }

  async function stop() {
    const ok = window.confirm(
      "确认停止当前任务？\n\n会结束 control 启动的进程组，也会尝试停止外部 supervisor（flock pid）。\n生产 batch 若在跑会被杀掉。",
    );
    if (!ok) {
      showOpsFeedback("已取消停止", "info", { toast: true, sticky: false });
      return;
    }
    setBusyKey("stop");
    showOpsFeedback("正在停止…", "warn", { toast: false, sticky: true });
    try {
      const data = await api.stopRun();
      const detail =
        (data && data.detail) || (data && data.ok ? "stopped" : "no active run");
      const pid = data && data.pid;
      showOpsFeedback(
        `${data && data.ok ? "已停止" : "停止未生效"} · ${detail}${pid != null ? ` pid=${pid}` : ""}`,
        data && data.ok ? "ok" : "warn",
      );
      const cur = await api.currentRun();
      currentRunState.value = cur.run ?? cur ?? null;
    } catch (e) {
      if (e.status === 401) {
        session.value = { ...session.value, authenticated: false };
        return;
      }
      showOpsFeedback(formatApiError(e), "err");
    } finally {
      setBusyKey(null);
    }
  }

  async function testProxy() {
    setBusyKey("proxy");
    showOpsFeedback("正在测代理…", "info", { toast: false, sticky: true });
    try {
      let data;
      let via = "clash";
      try {
        data = await api.testClash({ limit: 8 });
      } catch {
        via = "catalog";
        data = await api.testCatalog({ limit: 8 });
      }
      const healthy =
        data && (data.healthy != null
          ? data.healthy
          : data.ok_count != null
            ? data.ok_count
            : null);
      const total =
        data && (data.total != null
          ? data.total
          : data.tested != null
            ? data.tested
            : null);
      const summary =
        healthy != null && total != null
          ? `healthy=${healthy}/${total}`
          : (data && data.detail) || "done";
      showOpsFeedback(`代理测试完成 (${via}) · ${summary}`, "ok");
      setActionResult(typeof data === "string" ? data : JSON.stringify(data, null, 2));
    } catch (e) {
      if (e.status === 401) {
        session.value = { ...session.value, authenticated: false };
        return;
      }
      showOpsFeedback(`测代理失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <section class="page">
      <header class="page-head">
        <div>
          <h1>协议注册</h1>
          <p class="hint">左启动参数 · 右实时进度。完整 worker/supervisor 日志 → 日志页。</p>
        </div>
        <div class="toolbar">
          <button
            type="button"
            class="btn btn-primary btn-md"
            disabled={busyKey === "start" || !regFormLoaded.value}
            title={!regFormLoaded.value ? "请先加载配置" : undefined}
            onClick={start}
          >
            {busyKey === "start" ? "启动中…" : "开始"}
          </button>
          <button
            type="button"
            class="btn btn-danger btn-md"
            disabled={busyKey === "stop"}
            onClick={stop}
          >
            {busyKey === "stop" ? "停止中…" : "停止"}
          </button>
          <button
            type="button"
            class="btn btn-ghost btn-md"
            disabled={busyKey === "refresh"}
            onClick={refresh}
          >
            {busyKey === "refresh" ? "刷新中…" : "刷新"}
          </button>
          <button
            type="button"
            class="btn btn-ghost btn-md"
            onClick={gotoLogs}
          >
            日志 →
          </button>
        </div>
      </header>

      {/* Sticky banner + ops log live in App shell OpsFeedbackBar (all pages). */}

      <div class="split">
        <div class="panel form-panel">
          <RegForm
            value={form}
            residualPrimary={primaryProvider}
            onChange={(next) => setForm(next)}
            advancedOpen={advancedOpen}
            onToggleAdvanced={(e) => setAdvancedOpen(e.currentTarget.open)}
          />
          {/* Footer toolbar: 保存 / 自检(link #/settings) / 测代理.
              No duplicate cleanup/selfcheck buttons (settings only). */}
          <div class="form-foot-toolbar">
            <button
              type="button"
              class="btn btn-ghost btn-sm"
              disabled={busyKey === "save" || !regFormLoaded.value}
              title={!regFormLoaded.value ? "请先加载配置" : undefined}
              onClick={() => saveConfig().catch(() => {})}
            >
              {busyKey === "save" ? "保存中…" : "保存"}
            </button>
            <a class="btn btn-ghost btn-sm" href="#/settings" title="自检 / 清理在设置页">
              自检 →
            </a>
            <button
              type="button"
              class="btn btn-ghost btn-sm"
              disabled={busyKey === "proxy"}
              onClick={testProxy}
            >
              {busyKey === "proxy" ? "测代理…" : "测代理"}
            </button>
          </div>
          {actionResult ? (
            <pre class={`log compact ${actionResult ? "ok" : ""}`}>{actionResult}</pre>
          ) : null}
        </div>

        <div class="panel progress-panel">
          <RunProgress onGotoLogs={gotoLogs} />
        </div>
      </div>
    </section>
  );
}
