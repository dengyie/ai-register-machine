// MailTab — per-provider email config + hotmail cred import + pool probe.
// Pipeline multi-select / strategy live on Settings; Register only picks pool.
import { useCallback, useEffect, useMemo, useState } from "preact/hooks";
import * as api from "../../api/client.js";
import { session } from "../../store/session.js";
import { showOpsFeedback } from "../../store/feedback.js";
import { Button } from "../../ui/index.js";
import { formatApiError } from "../../lib/format.js";

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

function pretty(v) {
  try {
    return typeof v === "string" ? v : JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function isQuarantinable(row) {
  if (!row) return false;
  if (typeof row.quarantinable === "boolean") return row.quarantinable;
  return (
    row.status === "grant_expired" ||
    row.status === "refresh_invalid" ||
    row.status === "abuse_mode"
  );
}

function statusLabel(row) {
  if (!row) return "—";
  if (row.status === "ok") return "好用";
  if (isQuarantinable(row)) return "挂了";
  return "不定";
}

function parseDomainList(raw) {
  return String(raw || "")
    .replace(/，/g, ",")
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function joinDomains(list) {
  const out = [];
  const seen = new Set();
  for (const d of list || []) {
    const name = String(d || "").trim();
    if (!name) continue;
    const low = name.toLowerCase();
    if (seen.has(low)) continue;
    seen.add(low);
    out.push(name);
  }
  return out.join(",");
}

export function MailTab() {
  const [form, setForm] = useState(EMPTY);
  const [result, setResult] = useState("");
  const [credText, setCredText] = useState("");
  const [credMode, setCredMode] = useState("append");
  const [busy, setBusy] = useState("");
  // Block Save until GET /api/config succeeds — empty initial form must not
  // clear defaultDomains via clearable empty write-through.
  const [hydrated, setHydrated] = useState(false);
  const [dirty, setDirty] = useState(false);
  // Last successful load domains — detect intentional clear on save.
  const [baselineDomains, setBaselineDomains] = useState("");
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

  // Pool probe state
  const [pool, setPool] = useState(null);
  const [probeDomains, setProbeDomains] = useState([]);
  const [probeLimit, setProbeLimit] = useState(30);
  const [probeOut, setProbeOut] = useState(null);
  const [selected, setSelected] = useState({}); // email -> bool

  function auth(e) {
    if (e && e.status === 401) {
      session.value = { ...session.value, authenticated: false };
      return true;
    }
    return false;
  }

  const loadPool = useCallback(async () => {
    try {
      const data = await api.mailPoolStats();
      setPool(data);
      const known = data.known_domains || [];
      const present = Object.keys(data.by_domain || {});
      const defaults = known.filter((d) => present.includes(d));
      setProbeDomains((prev) =>
        prev.length ? prev : defaults.length ? defaults : known.slice(0, 2),
      );
    } catch (e) {
      if (auth(e)) return;
      setPool(null);
    }
  }, []);

  const load = useCallback(async ({ force = false } = {}) => {
    if (force && dirty) {
      if (
        !window.confirm("有未保存的邮箱配置更改，确认丢弃并重新加载？")
      ) {
        showOpsFeedback("已取消重载", "info", { toast: true, sticky: false });
        return;
      }
    }
    setBusy("load");
    try {
      const data = await api.getConfig();
      const c = data.config || {};
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
      setForm(next);
      setCfSelected(parseDomainList(next.defaultDomains));
      setBaselineDomains(String(next.defaultDomains || ""));
      setDirty(false);
      setHydrated(true);
      setResult(
        pretty({
          loaded: true,
          domains: c.defaultDomains,
          note: "链路池/策略在设置页；本页只细配各 Provider",
        }),
      );
      await loadPool();
    } catch (e) {
      // Always clear hydrated on failure (incl. 401) so Save stays blocked.
      setHydrated(false);
      if (auth(e)) return;
      setResult(String(e.message || e));
      showOpsFeedback(`加载邮箱配置失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }, [dirty, loadPool]);

  useEffect(() => {
    load();
    // initial mount only
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function set(partial) {
    setDirty(true);
    setForm((p) => ({ ...p, ...partial }));
  }

  function togglePanel(name) {
    setOpenPanels((p) => ({ ...p, [name]: !p[name] }));
  }

  async function save() {
    if (!hydrated || busy === "load") {
      showOpsFeedback("请先加载配置后再保存（避免空表单清空 defaultDomains）", "err", {
        toast: true,
        sticky: true,
      });
      return;
    }
    // Single source of truth for domains:
    // - form.defaultDomains is always kept in sync with chips / hand-edit.
    // - empty string is intentional clear (backend defaultDomains is clearable).
    const domains = joinDomains(parseDomainList(form.defaultDomains));
    const baselineJoined = joinDomains(parseDomainList(baselineDomains));
    if (!domains && baselineJoined) {
      if (
        !window.confirm(
          "确认清空 defaultDomains？\n\n将写入空值到 config 与 .env（DEFAULT_DOMAINS=）。\nCloudflare / CloudMail / Gmail catch-all 将无可用域名。",
        )
      ) {
        showOpsFeedback("已取消保存", "info", { toast: true, sticky: false });
        return;
      }
    }
    setBusy("save");
    try {
      const partial = {};
      partial.defaultDomains = domains;

      for (const [k, raw] of Object.entries(form)) {
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
      const data = await api.putConfig({ config: partial });
      setResult(pretty(data));
      const saved = data.config || {};
      const savedDomains =
        saved.defaultDomains != null ? String(saved.defaultDomains) : domains;
      setForm((p) => {
        const n = { ...p };
        for (const k of SECRET_KEYS) n[k] = "";
        n.defaultDomains = savedDomains;
        return n;
      });
      setCfSelected(parseDomainList(savedDomains));
      setBaselineDomains(savedDomains);
      setDirty(false);
      showOpsFeedback(
        domains
          ? `邮箱配置已保存 · domains=${domains}`
          : "邮箱配置已保存 · defaultDomains 已清空",
        "ok",
      );
    } catch (e) {
      if (auth(e)) {
        setHydrated(false);
        return;
      }
      setResult(String(e.message || e));
      showOpsFeedback(`保存失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
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
          setDirty(true);
          setForm((p) => ({ ...p, defaultDomains: joined }));
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
      if (auth(e)) return;
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
    setDirty(true);
    setForm((p) => ({ ...p, defaultDomains: joined }));
    setCfSelected(parseDomainList(joined));
  }

  async function importCreds() {
    setBusy("cred");
    try {
      const fd = new FormData();
      fd.append("content", credText || "");
      fd.append("mode", credMode || "append");
      const body = await api.importMailText(fd);
      setResult(pretty(body));
      const r = (body && body.result) || body || {};
      const summary =
        (body && body.detail) ||
        r.summary ||
        `导入完成 · 新增 ${r.new ?? r.lines_written ?? 0} · 重复 ${r.duplicate ?? 0} · 无效 ${r.skipped ?? 0}`;
      const status = r.status || (r.new > 0 || r.lines_written > 0 ? "success" : "empty");
      const kind = status === "success" ? "ok" : status === "partial" ? "info" : "info";
      showOpsFeedback(summary, kind, { toast: true, sticky: true });
      await loadPool();
    } catch (e) {
      if (auth(e)) return;
      setResult(pretty({ error: formatApiError(e) }));
      showOpsFeedback(`导入失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  function toggleDomain(dom) {
    setProbeDomains((prev) => {
      if (prev.includes(dom)) return prev.filter((d) => d !== dom);
      return [...prev, dom];
    });
  }

  async function runProbe() {
    if (!probeDomains.length) {
      showOpsFeedback("请先勾选至少一个域名（全不选不会测全部）", "info");
      return;
    }
    setBusy("probe");
    try {
      const body = {
        domains: probeDomains,
        limit: Math.max(1, Math.min(200, Number(probeLimit) || 30)),
        concurrency: 4,
        wall_seconds: 90,
      };
      const data = await api.probeMail(body);
      setProbeOut(data);
      const nextSel = {};
      for (const r of data.results || []) {
        if (isQuarantinable(r)) nextSel[r.email] = true;
      }
      setSelected(nextSel);
      const qn =
        data.quarantinable != null
          ? data.quarantinable
          : (data.results || []).filter(isQuarantinable).length;
      const msg =
        `探测完成: 好用 ${data.ok || 0} / 挂了可移 ${qn} / 不定 ${Math.max(0, (data.dead || 0) - qn)}` +
        (data.timed_out ? " · 超时截断" : "");
      showOpsFeedback(msg, data.dead ? "info" : "ok");
      setResult(
        pretty({
          probed: data.probed,
          ok: data.ok,
          dead: data.dead,
          quarantinable: qn,
          timed_out: data.timed_out,
          by_status: data.by_status,
        }),
      );
    } catch (e) {
      if (auth(e)) return;
      showOpsFeedback(`探测失败: ${formatApiError(e)}`, "err");
      setResult(pretty({ error: formatApiError(e) }));
    } finally {
      setBusy("");
    }
  }

  function selectAllDead() {
    const next = {};
    for (const r of (probeOut && probeOut.results) || []) {
      if (isQuarantinable(r)) next[r.email] = true;
    }
    setSelected(next);
  }

  function clearSelection() {
    setSelected({});
  }

  function toggleRow(email) {
    setSelected((prev) => ({ ...prev, [email]: !prev[email] }));
  }

  const selectedEmails = useMemo(
    () => Object.keys(selected).filter((k) => selected[k]),
    [selected],
  );

  async function doCompact(dryRun = false) {
    if (!dryRun) {
      const ok = window.confirm(
        "确认精简邮箱池？\n\n" +
          "· 按邮箱去重（保留首次出现）\n" +
          "· 去掉无效行\n" +
          "· 先备份 mail_credentials.txt.bak-compact-*\n" +
          "· 不触碰 dead 归档",
      );
      if (!ok) return;
    }
    setBusy("compact");
    try {
      const data = await api.compactMail({
        dry_run: !!dryRun,
        drop_invalid: true,
        drop_comments: false,
      });
      setResult(pretty(data));
      const summary =
        data.summary ||
        `${dryRun ? "预览" : "精简"} · 唯一 ${data.unique ?? "—"} · 重复 ${data.duplicate_extra ?? 0}`;
      showOpsFeedback(summary, data.changed ? "ok" : "info", {
        toast: true,
        sticky: true,
      });
      await loadPool();
    } catch (e) {
      if (auth(e)) return;
      showOpsFeedback(`精简失败: ${formatApiError(e)}`, "err");
      setResult(pretty({ error: formatApiError(e) }));
    } finally {
      setBusy("");
    }
  }

  async function doQuarantine() {
    if (!selectedEmails.length) {
      showOpsFeedback("请先勾选要移出的邮箱", "info");
      return;
    }
    const rows = (probeOut && probeOut.results) || [];
    const byEmail = Object.fromEntries(
      rows.map((r) => [String(r.email || "").toLowerCase(), r]),
    );
    const hard = [];
    const soft = [];
    for (const e of selectedEmails) {
      const r = byEmail[e.toLowerCase()];
      if (r && isQuarantinable(r)) hard.push(e);
      else soft.push(e);
    }
    if (!hard.length) {
      showOpsFeedback(
        "选中项都不是可移出状态（grant_expired / refresh_invalid / abuse_mode）",
        "info",
      );
      return;
    }
    const softNote = soft.length
      ? `\n另有 ${soft.length} 个网络/未知失败已自动跳过。`
      : "";
    const ok = window.confirm(
      `确认移出 ${hard.length} 个确认挂了的邮箱？${softNote}\n\n` +
        `它们会从主池删除，并追加到 mail_credentials.dead.txt（先备份主池）。\n` +
        `仅 grant_expired / refresh_invalid / abuse_mode 会被移出。`,
    );
    if (!ok) return;
    setBusy("quarantine");
    try {
      const data = await api.quarantineMail({
        emails: hard,
        reason: "probe:ui",
      });
      showOpsFeedback(
        `已移出 ${data.removed || 0} 个（未找到 ${data.not_found || 0}）`,
        "ok",
      );
      setResult(pretty(data));
      if (probeOut && probeOut.results) {
        const gone = new Set(hard.map((e) => e.toLowerCase()));
        const left = probeOut.results.filter(
          (r) => !gone.has(String(r.email || "").toLowerCase()),
        );
        setProbeOut({
          ...probeOut,
          results: left,
          probed: left.length,
          ok: left.filter((r) => r.status === "ok").length,
          dead: left.filter((r) => r.status !== "ok").length,
          quarantinable: left.filter(isQuarantinable).length,
        });
      }
      setSelected({});
      await loadPool();
    } catch (e) {
      if (auth(e)) return;
      showOpsFeedback(`移出失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  const domainChips = useMemo(() => {
    const known = (pool && pool.known_domains) || [
      "hotmail.com",
      "outlook.com",
      "live.com",
      "msn.com",
    ];
    const counts = (pool && pool.by_domain) || {};
    return known.map((d) => ({
      domain: d,
      count: counts[d] || 0,
    }));
  }, [pool]);

  const selectedDomainSet = useMemo(() => {
    return new Set(cfSelected.map((d) => d.toLowerCase()));
  }, [cfSelected]);

  function panel(name, title, body) {
    const open = !!openPanels[name];
    return (
      <details
        class="card provider-panel"
        open={open}
        onToggle={(e) => {
          const next = e.currentTarget.open;
          setOpenPanels((p) => ({ ...p, [name]: next }));
        }}
      >
        <summary class="provider-panel-summary" onClick={(e) => e.preventDefault()}>
          <button type="button" class="provider-panel-toggle" onClick={() => togglePanel(name)}>
            {open ? "▾" : "▸"} {title}
          </button>
        </summary>
        {open ? <div class="provider-panel-body grid mail-form">{body}</div> : null}
      </details>
    );
  }

  return (
    <div class="resources-tab mail-tab">
      <div class="toolbar wrap mail-toolbar">
        <Button
          variant="ghost"
          busy={busy === "load"}
          onClick={() => load({ force: true })}
        >
          重载
        </Button>
        <Button
          variant="primary"
          busy={busy === "save"}
          disabled={!hydrated || busy === "load"}
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
        每个 Provider 只显示有意义的字段。全局 <code>defaultDomains</code> 由
        Cloudflare 勾选 / CloudMail 手填共用（保存空值会清空）；yyds / duckmail
        域名由各自 API 拉取，不在此配置。链路勾选与策略在
        <a href="#/settings">设置</a>
        （与注册页共用 <code>email_providers</code>）。
        {!hydrated ? (
          <span class="hint warn"> 配置未加载，保存已禁用。</span>
        ) : dirty ? (
          <span class="hint warn"> 有未保存更改。</span>
        ) : null}
      </p>

      {panel(
        "cloudflare",
        "Cloudflare 临时邮",
        <>
          <label>
            cloudflare_api_base
            <input
              value={form.cloudflare_api_base}
              placeholder="https://mail-api.example.com"
              onInput={(e) => set({ cloudflare_api_base: e.currentTarget.value })}
            />
          </label>
          <label>
            cloudflare_api_key
            <input
              type="password"
              value={form.cloudflare_api_key}
              placeholder="leave empty to keep"
              onInput={(e) => set({ cloudflare_api_key: e.currentTarget.value })}
            />
          </label>
          <div class="span2">
            <div class="toolbar wrap">
              <Button
                variant="ghost"
                busy={busy === "cfDomains"}
                onClick={fetchCfDomains}
              >
                从 Worker 拉取域名
              </Button>
              <span class="hint tight">
                {cfMeta
                  ? `已拉 ${cfMeta.count ?? cfDomains.length} · base=${cfMeta.api_base || "—"} · key=${cfMeta.has_api_key ? "有" : "无"} · 不自动全选`
                  : "保存 base/key 后再拉；手动勾选写入 defaultDomains（不自动全选）"}
              </span>
            </div>
            <div class="mail-domain-chips" style={{ marginTop: "0.5rem" }}>
              {(cfDomains.length ? cfDomains : parseDomainList(form.defaultDomains)).map(
                (d) => (
                  <label key={d} class="check chip">
                    <input
                      type="checkbox"
                      checked={selectedDomainSet.has(String(d).toLowerCase())}
                      onChange={() => toggleCfDomain(d)}
                    />{" "}
                    {d}
                  </label>
                ),
              )}
              {!cfDomains.length && !parseDomainList(form.defaultDomains).length ? (
                <span class="hint">尚无域名 · 拉取或手填下方</span>
              ) : null}
            </div>
          </div>
          <label class="span2">
            defaultDomains（全局 · CF 勾选结果 · 可清空）
            <input
              value={form.defaultDomains}
              placeholder="a.com,b.com · 留空保存即清空"
              onInput={(e) => {
                const v = e.currentTarget.value;
                set({ defaultDomains: v });
                setCfSelected(parseDomainList(v));
              }}
            />
          </label>
        </>,
      )}

      {panel(
        "cloudmail",
        "CloudMail 自建",
        <>
          <label>
            cloudmail_url
            <input
              value={form.cloudmail_url}
              placeholder="https://mail.example.com"
              onInput={(e) => set({ cloudmail_url: e.currentTarget.value })}
            />
          </label>
          <label>
            cloudmail_admin_email
            <input
              value={form.cloudmail_admin_email}
              onInput={(e) => set({ cloudmail_admin_email: e.currentTarget.value })}
            />
          </label>
          <label>
            cloudmail_password
            <input
              type="password"
              value={form.cloudmail_password}
              placeholder="leave empty to keep"
              onInput={(e) => set({ cloudmail_password: e.currentTarget.value })}
            />
          </label>
          <label class="span2">
            defaultDomains（与 CF 共用全局 · 可清空）
            <input
              value={form.defaultDomains}
              placeholder="a.com,b.com · 留空保存即清空"
              onInput={(e) => {
                const v = e.currentTarget.value;
                set({ defaultDomains: v });
                setCfSelected(parseDomainList(v));
              }}
            />
          </label>
        </>,
      )}

      {panel(
        "duckmail",
        "DuckMail",
        <>
          <label class="span2">
            duckmail_api_key
            <input
              type="password"
              value={form.duckmail_api_key}
              placeholder="leave empty to keep"
              onInput={(e) => set({ duckmail_api_key: e.currentTarget.value })}
            />
          </label>
          <p class="span2 hint tight">域名由 DuckMail API /domains 自动获取，无需填写 defaultDomains。</p>
        </>,
      )}

      {panel(
        "yyds",
        "yydsmail",
        <>
          <label class="span2">
            yyds_api_key
            <input
              type="password"
              value={form.yyds_api_key}
              placeholder="leave empty to keep"
              onInput={(e) => set({ yyds_api_key: e.currentTarget.value })}
            />
          </label>
          <p class="span2 hint tight">
            域名由 yyds API 拉取。jwt 若需要请写 .env 的 YYDS_JWT（不在此暴露）。
          </p>
        </>,
      )}

      {panel(
        "gmail",
        "Gmail catch-all（IMAP）",
        <>
          <p class="span2 hint">
            表单不写应用密码。在项目 <code>.env</code> 配置{" "}
            <code>GMAIL_IMAP_USER</code> / <code>GMAIL_IMAP_PASSWORD</code>
            ；catch-all 域名用上方全局 defaultDomains（CF/CloudMail 面板）。
          </p>
          <label>
            gmail_imap_user（可选提示）
            <input
              value={form.gmail_imap_user}
              placeholder="可选 · 也可只写 .env"
              onInput={(e) => set({ gmail_imap_user: e.currentTarget.value })}
            />
          </label>
        </>,
      )}

      {panel(
        "hotmail",
        "Hotmail / Outlook",
        <>
          <label>
            hotmail_accounts_file
            <input
              value={form.hotmail_accounts_file}
              placeholder="mail_credentials.txt"
              onInput={(e) => set({ hotmail_accounts_file: e.currentTarget.value })}
            />
          </label>
          <label class="check span2">
            <input
              type="checkbox"
              checked={!!form.hotmail_allow_plus_alias}
              onChange={(e) =>
                set({ hotmail_allow_plus_alias: e.currentTarget.checked })
              }
            />{" "}
            hotmail_allow_plus_alias（生产勿开）
          </label>
          <p class="span2 hint tight">
            凭证格式：<code>邮箱----密码----ClientID----refresh_token</code>
            。不使用 defaultDomains。下方可批量导入。
          </p>
        </>,
      )}

      <div class="card">
        <h2>Hotmail / Outlook 凭证导入</h2>
        <p class="hint">
          支持四段 <code>email----password----clientId----token</code>、JSON / CSV /
          管道分隔；append 自动去重。导入结果会显示新增 / 重复 / 无效条数。
        </p>
        <textarea
          rows={6}
          value={credText}
          placeholder="email----password----clientId----refresh_token"
          onInput={(e) => setCredText(e.currentTarget.value)}
        />
        <label class="inline">
          mode{" "}
          <select value={credMode} onChange={(e) => setCredMode(e.currentTarget.value)}>
            <option value="append">append</option>
            <option value="replace">replace</option>
          </select>
        </label>
        <Button variant="ghost" busy={busy === "cred"} onClick={importCreds}>
          导入凭证
        </Button>
      </div>

      <div class="card mail-probe">
        <h2>邮箱凭据探测（OAuth 刷新）</h2>
        <p class="hint">
          只测 refresh_token 是否还能换 access_token；成功=好用。仅 grant_expired /
          refresh_invalid / abuse_mode 默认可移出；网络超时标为「不定」不默认勾选。不会返回密码/token。
        </p>

        <div class="mail-probe-kpis">
          <span>
            主池唯一 <strong>{pool ? pool.total : "—"}</strong>
          </span>
          {pool && pool.raw_lines != null && pool.raw_lines !== pool.total ? (
            <span>
              文件行 <strong>{pool.raw_lines}</strong>
            </span>
          ) : null}
          {pool && pool.duplicate_extra ? (
            <span class="warn">
              重复多余 <strong>{pool.duplicate_extra}</strong>
            </span>
          ) : null}
          {pool && pool.invalid_lines ? (
            <span class="warn">
              无效行 <strong>{pool.invalid_lines}</strong>
            </span>
          ) : null}
          <span>
            归档 dead <strong>{pool ? pool.dead_total : "—"}</strong>
          </span>
          {pool && pool.by_domain
            ? Object.entries(pool.by_domain).map(([d, n]) => (
                <span key={d}>
                  {d} <strong>{n}</strong>
                </span>
              ))
            : null}
        </div>

        <div class="mail-probe-controls toolbar wrap">
          <div class="mail-domain-chips">
            {domainChips.map(({ domain, count }) => (
              <label key={domain} class="check chip">
                <input
                  type="checkbox"
                  checked={probeDomains.includes(domain)}
                  onChange={() => toggleDomain(domain)}
                  disabled={busy === "probe"}
                />{" "}
                {domain}
                {count ? ` (${count})` : ""}
              </label>
            ))}
          </div>
          <label class="inline">
            limit{" "}
            <input
              type="number"
              min={1}
              max={200}
              value={probeLimit}
              style={{ width: "5rem" }}
              disabled={busy === "probe"}
              onInput={(e) => setProbeLimit(e.currentTarget.value)}
            />
          </label>
          <Button
            variant="primary"
            busy={busy === "probe"}
            onClick={runProbe}
            disabled={busy === "probe" || !probeDomains.length}
          >
            开始探测（验证可用性）
          </Button>
          <Button variant="ghost" onClick={loadPool} disabled={busy === "probe"}>
            刷新统计
          </Button>
          <Button
            variant="ghost"
            busy={busy === "compact"}
            onClick={() => doCompact(true)}
            disabled={busy === "compact" || busy === "probe"}
          >
            预览精简
          </Button>
          <Button
            variant="ghost"
            busy={busy === "compact"}
            onClick={() => doCompact(false)}
            disabled={
              busy === "compact" ||
              busy === "probe" ||
              !(pool && (pool.needs_compact || pool.duplicate_extra || pool.invalid_lines))
            }
            title={
              pool && pool.needs_compact
                ? "按邮箱去重并去掉无效行"
                : "当前无需精简（无重复/无效行）"
            }
          >
            精简去重
            {pool && pool.duplicate_extra ? ` (−${pool.duplicate_extra})` : ""}
          </Button>
        </div>

        {probeOut ? (
          <div class="mail-probe-results">
            <div class="toolbar wrap">
              <span>
                结果: 探测 {probeOut.probed} · 好用 {probeOut.ok} · 可移出{" "}
                {probeOut.quarantinable != null
                  ? probeOut.quarantinable
                  : (probeOut.results || []).filter(isQuarantinable).length}
                {" · 不定 "}
                {Math.max(
                  0,
                  (probeOut.dead || 0) -
                    (probeOut.quarantinable != null
                      ? probeOut.quarantinable
                      : (probeOut.results || []).filter(isQuarantinable).length),
                )}
                {probeOut.timed_out ? " · 超时截断" : ""}
              </span>
              <Button variant="ghost" onClick={selectAllDead}>
                全选可移出
              </Button>
              <Button variant="ghost" onClick={clearSelection}>
                取消勾选
              </Button>
              <Button
                variant="danger"
                busy={busy === "quarantine"}
                onClick={doQuarantine}
                disabled={!selectedEmails.length || busy === "quarantine"}
              >
                移出选中 ({selectedEmails.length})
              </Button>
            </div>
            <div class="table-wrap">
              <table class="data-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>email</th>
                    <th>domain</th>
                    <th>状态</th>
                    <th>reason</th>
                  </tr>
                </thead>
                <tbody>
                  {(probeOut.results || []).map((r) => {
                    const q = isQuarantinable(r);
                    const soft = r.status !== "ok" && !q;
                    const rowClass = r.status === "ok" ? "ok" : q ? "dead" : "soft";
                    const badgeClass =
                      r.status === "ok" ? "badge ok" : q ? "badge bad" : "badge soft";
                    return (
                      <tr key={r.email} class={rowClass}>
                        <td>
                          <input
                            type="checkbox"
                            checked={!!selected[r.email]}
                            onChange={() => toggleRow(r.email)}
                            disabled={r.status === "ok"}
                          />
                        </td>
                        <td>{r.email}</td>
                        <td>{r.domain}</td>
                        <td>
                          <span class={badgeClass}>
                            {statusLabel(r)}
                            {r.status !== "ok" ? ` / ${r.status}` : ""}
                            {soft ? " · 不默认移出" : ""}
                          </span>
                        </td>
                        <td class="muted" title={r.ms_error || r.reason || ""}>
                          {(r.ms_error || r.reason || "").slice(0, 80)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </div>

      {result ? <pre class="log">{result}</pre> : null}
    </div>
  );
}
