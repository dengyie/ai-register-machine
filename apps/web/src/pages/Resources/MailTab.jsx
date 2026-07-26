// MailTab — per-provider email config + hotmail cred import + pool probe.
// Pipeline multi-select / strategy live on Settings; Register only picks pool.
import { useCallback, useEffect, useState } from "preact/hooks";
import * as api from "../../api/client.js";
import { showOpsFeedback } from "../../store/feedback.js";
import { Button, LogDetails } from "../../ui/index.js";
import { formatApiError } from "../../lib/format.js";
import { pretty, auth401 } from "../../lib/http.js";
import { useConfigForm } from "../../lib/useConfigForm.js";
import { parseDomainList, joinDomains } from "./mail/domains.js";
import { DefaultDomainsCard } from "./mail/DefaultDomainsCard.jsx";
import { MailProviderPanels } from "./mail/MailProviderPanels.jsx";
import { MailCredImport } from "./mail/MailCredImport.jsx";
import { MailPoolProbe } from "./mail/MailPoolProbe.jsx";

const SECRET_KEYS = [
  "cloudflare_api_key",
  "duckmail_api_key",
  "yyds_api_key",
  "cloudmail_password",
];

const EMPTY = {
  defaultDomains: "",
  cloudflare_api_base: "",
  cloudflare_api_key: "",
  duckmail_api_key: "",
  yyds_api_key: "",
  cloudmail_url: "",
  cloudmail_admin_email: "",
  cloudmail_password: "",
  gmail_imap_user: "",
  hotmail_accounts_file: "",
  hotmail_allow_plus_alias: false,
};

export function MailTab() {
  const [result, setResult] = useState("");
  const [openPanels, setOpenPanels] = useState({
    cloudflare: true,
    cloudmail: false,
    duckmail: false,
    yyds: false,
    gmail: false,
    hotmail: true,
  });
  const [cfDomains, setCfDomains] = useState([]);
  const [cfSelected, setCfSelected] = useState([]);
  const [cfMeta, setCfMeta] = useState(null);

  // Pool stats owned here — cred import and probe both invalidate them.
  const [pool, setPool] = useState(null);

  const loadPool = useCallback(async () => {
    try {
      const data = await api.mailPoolStats();
      setPool(data);
    } catch (e) {
      if (auth401(e)) return;
      setPool(null);
    }
  }, []);

  const cfg = useConfigForm({
    empty: EMPTY,
    // Last successful load domains — detect intentional clear on save.
    // (String, not object — kept as-is from the pre-hook implementation.)
    initialBaseline: "",
    loadRequest: async () => {
      const data = await api.getConfig();
      return data.config || {};
    },
    hydrate: (c) => {
      const next = { ...EMPTY };
      for (const k of Object.keys(EMPTY)) {
        if (c[k] == null) continue;
        if (k === "hotmail_allow_plus_alias") {
          next[k] = c[k] === true || c[k] === "true" || c[k] === 1 || c[k] === "1";
        } else if (SECRET_KEYS.includes(k)) {
          next[k] = "";
        } else {
          next[k] = String(c[k]);
        }
      }
      return { form: next, baseline: String(next.defaultDomains || "") };
    },
    onLoaded: async (c, next) => {
      setCfSelected(parseDomainList(next.defaultDomains));
      setResult(
        pretty({
          loaded: true,
          domains: c.defaultDomains,
          note: "链路池/策略在设置页；本页只细配各 Provider",
        }),
      );
      await loadPool();
    },
    onLoadError: (e) => {
      setResult(String(e.message || e));
      showOpsFeedback(`加载邮箱配置失败: ${formatApiError(e)}`, "err");
    },
    confirmReload: {
      message: "有未保存的邮箱配置更改，确认丢弃并重新加载？",
      onCancel: () =>
        showOpsFeedback("已取消重载", "info", { toast: true, sticky: false }),
    },
    onSaveBlocked: () =>
      showOpsFeedback("请先加载配置后再保存（避免空表单清空 defaultDomains）", "err", {
        toast: true,
        sticky: true,
      }),
    // Single source of truth for domains:
    // - form.defaultDomains is always kept in sync with chips / hand-edit.
    // - empty string is intentional clear (backend defaultDomains is clearable).
    confirmClear: (f, baselineDomains) => {
      const domains = joinDomains(parseDomainList(f.defaultDomains));
      const baselineJoined = joinDomains(parseDomainList(baselineDomains));
      if (domains || !baselineJoined) return null;
      return {
        message:
          "确认清空 defaultDomains？\n\n将写入空值到 config 与 .env（DEFAULT_DOMAINS=）。\nCloudflare / CloudMail / Gmail catch-all 将无可用域名。",
      };
    },
    onSaveCancelled: () =>
      showOpsFeedback("已取消保存", "info", { toast: true, sticky: false }),
    saveRequest: (f) => {
      const partial = {};
      partial.defaultDomains = joinDomains(parseDomainList(f.defaultDomains));

      for (const [k, raw] of Object.entries(f)) {
        if (k === "defaultDomains") continue;
        if (SECRET_KEYS.includes(k)) {
          const v = String(raw || "");
          if (v === "" || v.startsWith("***")) continue;
          partial[k] = v;
          continue;
        }
        if (k === "hotmail_allow_plus_alias") {
          partial[k] = !!raw;
          continue;
        }
        if (raw === "" || raw == null) continue;
        partial[k] = raw;
      }
      return api.putConfig({ config: partial });
    },
    onSaved: (data, f) => {
      const domains = joinDomains(parseDomainList(f.defaultDomains));
      setResult(pretty(data));
      const saved = data.config || {};
      const savedDomains =
        saved.defaultDomains != null ? String(saved.defaultDomains) : domains;
      setCfSelected(parseDomainList(savedDomains));
      showOpsFeedback(
        domains
          ? `邮箱配置已保存 · domains=${domains}`
          : "邮箱配置已保存 · defaultDomains 已清空",
        "ok",
      );
      return {
        form: (p) => {
          const n = { ...p };
          for (const k of SECRET_KEYS) n[k] = "";
          n.defaultDomains = savedDomains;
          return n;
        },
        baseline: savedDomains,
      };
    },
    onSaveError: (e) => {
      setResult(String(e.message || e));
      showOpsFeedback(`保存失败: ${formatApiError(e)}`, "err");
    },
  });
  const { form, set, hydrated, dirty, load, save, busyIs, setBusy } = cfg;

  useEffect(() => {
    load();
    // initial mount only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function togglePanel(name, next) {
    setOpenPanels((p) => ({ ...p, [name]: next === undefined ? !p[name] : next }));
  }

  async function fetchCfDomains() {
    setBusy("cfDomains");
    try {
      const data = await api.cloudflareDomains();
      const list = data.domains || [];
      setCfDomains(list);
      setCfMeta({
        count: data.count,
        api_base: data.api_base,
        has_api_key: data.has_api_key,
      });
      // Keep form.defaultDomains as the only selection source.
      // Never auto-check the full Worker list — that can dump dozens of
      // domains into DEFAULT_DOMAINS on the next save. If form is empty but
      // server still has selected, mirror that known selection only.
      const fromForm = parseDomainList(form.defaultDomains);
      if (fromForm.length) {
        setCfSelected(fromForm);
      } else {
        const fromServer = Array.isArray(data.selected)
          ? data.selected.map((d) => String(d || "").trim()).filter(Boolean)
          : [];
        if (fromServer.length) {
          const joined = joinDomains(fromServer);
          set({ defaultDomains: joined });
          setCfSelected(parseDomainList(joined));
        } else {
          setCfSelected([]);
        }
      }
      showOpsFeedback(
        fromForm.length || (data.selected || []).length
          ? `已拉取 ${list.length} 个域名 · 勾选保持已有选择`
          : `已拉取 ${list.length} 个域名 · 请勾选后保存（不会自动全选）`,
        "ok",
      );
      setResult(pretty({ cloudflare_domains: list, selected: data.selected }));
    } catch (e) {
      if (auth401(e)) return;
      showOpsFeedback(`拉取域名失败: ${formatApiError(e)}`, "err");
      setResult(pretty({ error: formatApiError(e) }));
    } finally {
      setBusy("");
    }
  }

  function toggleCfDomain(dom) {
    // form.defaultDomains is the single source; cfSelected mirrors it.
    // Compute next outside setState updaters — no nested setState side effects.
    const cur = parseDomainList(form.defaultDomains);
    const low = String(dom).toLowerCase();
    const next = cur.some((d) => d.toLowerCase() === low)
      ? cur.filter((d) => d.toLowerCase() !== low)
      : [...cur, dom];
    const joined = joinDomains(next);
    set({ defaultDomains: joined });
    setCfSelected(parseDomainList(joined));
  }

  function editDomains(v) {
    set({ defaultDomains: v });
    setCfSelected(parseDomainList(v));
  }

  return (
    <div class="resources-tab mail-tab">
      <div class="toolbar wrap mail-toolbar">
        <Button
          variant="ghost"
          busy={busyIs("load")}
          onClick={() => load({ force: true })}
        >
          重载
        </Button>
        <Button
          variant="primary"
          busy={busyIs("save")}
          disabled={!hydrated || busyIs("load")}
          onClick={save}
          title={!hydrated ? "请先加载配置" : undefined}
        >
          保存邮箱配置
        </Button>
        <a class="btn btn-ghost btn-sm" href="#/settings">
          链路池 → 设置
        </a>
      </div>

      <p class="hint">
        本页只配各 Provider 的密钥 / 域名 / 凭证。链路勾选与轮询策略在
        <a href="#/settings">设置</a>
        （与注册页共用 <code>email_providers</code>）。
        {!hydrated ? (
          <span class="hint warn"> 配置未加载，保存已禁用。</span>
        ) : dirty ? (
          <span class="hint warn"> 有未保存更改。</span>
        ) : null}
      </p>

      <h2 class="mail-section-title">配置（改完要点「保存邮箱配置」）</h2>

      <DefaultDomainsCard
        form={form}
        cfDomains={cfDomains}
        cfMeta={cfMeta}
        cfSelected={cfSelected}
        onFetchCfDomains={fetchCfDomains}
        onToggleCfDomain={toggleCfDomain}
        onEditDomains={editDomains}
        busy={busyIs("cfDomains")}
      />

      <MailProviderPanels
        form={form}
        set={set}
        openPanels={openPanels}
        onTogglePanel={togglePanel}
      />

      <h2 class="mail-section-title">
        运维 <span class="hint tight">· 操作立即生效，不走「保存邮箱配置」</span>
      </h2>

      <MailCredImport onResult={setResult} onImported={loadPool} />

      <MailPoolProbe pool={pool} onReloadPool={loadPool} onResult={setResult} />

      <LogDetails text={result} summary="导入 / 探测响应详情" />
    </div>
  );
}
