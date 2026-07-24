// MailTab — email provider config + hotmail cred import + pool probe
import { useCallback, useEffect, useMemo, useState } from "preact/hooks";
import * as api from "../../api/client.js";
import { session } from "../../store/session.js";
import { showOpsFeedback } from "../../store/feedback.js";
import { Button, Select } from "../../ui/index.js";
import { formatApiError } from "../../lib/format.js";

const PROVIDERS = [
  "cloudflare",
  "cloudmail",
  "duckmail",
  "yyds",
  "gmail",
  "hotmail",
  "outlookmail",
];

const STRATEGIES = ["round_robin", "random", "failover"];

const SECRET_KEYS = [
  "cloudflare_api_key",
  "duckmail_api_key",
  "yyds_api_key",
  "cloudmail_password",
];

const EMPTY = {
  email_provider: "cloudflare",
  email_provider_strategy: "round_robin",
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

export function MailTab() {
  const [form, setForm] = useState(EMPTY);
  const [result, setResult] = useState("");
  const [credText, setCredText] = useState("");
  const [credMode, setCredMode] = useState("append");
  const [busy, setBusy] = useState("");

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
      setProbeDomains((prev) => (prev.length ? prev : defaults.length ? defaults : known.slice(0, 2)));
    } catch (e) {
      if (auth(e)) return;
      // pool file may be missing on fresh install
      setPool(null);
    }
  }, []);

  const load = useCallback(async () => {
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
          // show masked placeholder; empty submit keeps old
          next[k] = "";
        } else {
          next[k] = String(c[k]);
        }
      }
      setForm(next);
      setResult(
        pretty({
          loaded: true,
          provider: c.email_provider,
          domains: c.defaultDomains,
        }),
      );
      await loadPool();
    } catch (e) {
      if (auth(e)) return;
      setResult(String(e.message || e));
    } finally {
      setBusy("");
    }
  }, [loadPool]);

  useEffect(() => {
    load();
  }, [load]);

  function set(partial) {
    setForm((p) => ({ ...p, ...partial }));
  }

  async function save() {
    setBusy("save");
    try {
      // Mirror legacy collectForm: skip empty secrets + skip empty non-bool fields
      // so we never wipe stored keys with "".
      const partial = {};
      for (const [k, raw] of Object.entries(form)) {
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
      showOpsFeedback("邮箱配置已保存", "ok");
      // clear secret inputs after save
      setForm((p) => {
        const n = { ...p };
        for (const k of SECRET_KEYS) n[k] = "";
        return n;
      });
    } catch (e) {
      if (auth(e)) return;
      setResult(String(e.message || e));
      showOpsFeedback(`保存失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  async function importCreds() {
    setBusy("cred");
    try {
      const fd = new FormData();
      fd.append("content", credText || "");
      fd.append("mode", credMode || "append");
      const body = await api.importMailText(fd);
      setResult(pretty(body));
      showOpsFeedback("凭证已导入", "ok");
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
      // Default-check only hard-dead (quarantinable); skip network/unknown
      const nextSel = {};
      for (const r of data.results || []) {
        if (isQuarantinable(r)) nextSel[r.email] = true;
      }
      setSelected(nextSel);
      const qn = data.quarantinable != null
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
    // Soft-fail rows can be toggled in UI but doQuarantine only ships quarantinable.
    setSelected((prev) => ({ ...prev, [email]: !prev[email] }));
  }

  const selectedEmails = useMemo(
    () => Object.keys(selected).filter((k) => selected[k]),
    [selected],
  );

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

  return (
    <div class="resources-tab mail-tab">
      <div class="toolbar wrap mail-toolbar">
        <Button variant="ghost" busy={busy === "load"} onClick={load}>
          重载
        </Button>
        <Button
          variant="primary"
          busy={busy === "save"}
          onClick={save}
        >
          保存邮箱配置
        </Button>
      </div>

      <form class="card grid mail-form" onSubmit={(e) => e.preventDefault()}>
        <label>
          email_provider
          <Select
            value={form.email_provider}
            options={PROVIDERS}
            onChange={(v) => set({ email_provider: v })}
          />
        </label>
        <label>
          email_provider_strategy
          <Select
            value={form.email_provider_strategy}
            options={STRATEGIES}
            onChange={(v) => set({ email_provider_strategy: v })}
          />
        </label>
        <label class="span2">
          defaultDomains
          <input
            value={form.defaultDomains}
            placeholder="a.com,b.com"
            onInput={(e) => set({ defaultDomains: e.currentTarget.value })}
          />
        </label>
        <label>
          cloudflare_api_base
          <input
            value={form.cloudflare_api_base}
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
        <label>
          duckmail_api_key
          <input
            type="password"
            value={form.duckmail_api_key}
            placeholder="leave empty to keep"
            onInput={(e) => set({ duckmail_api_key: e.currentTarget.value })}
          />
        </label>
        <label>
          yyds_api_key
          <input
            type="password"
            value={form.yyds_api_key}
            placeholder="leave empty to keep"
            onInput={(e) => set({ yyds_api_key: e.currentTarget.value })}
          />
        </label>
        <label>
          cloudmail_url
          <input
            value={form.cloudmail_url}
            onInput={(e) => set({ cloudmail_url: e.currentTarget.value })}
          />
        </label>
        <label>
          cloudmail_admin_email
          <input
            value={form.cloudmail_admin_email}
            onInput={(e) =>
              set({ cloudmail_admin_email: e.currentTarget.value })
            }
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
        <label>
          gmail_imap_user
          <input
            value={form.gmail_imap_user}
            onInput={(e) => set({ gmail_imap_user: e.currentTarget.value })}
          />
        </label>
        <label>
          hotmail_accounts_file
          <input
            value={form.hotmail_accounts_file}
            placeholder="mail_credentials.txt"
            onInput={(e) =>
              set({ hotmail_accounts_file: e.currentTarget.value })
            }
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
      </form>

      <div class="card">
        <h2>Hotmail / Outlook 凭证导入</h2>
        <p class="hint">每行: email----password----clientId----refresh_token</p>
        <textarea
          rows={6}
          value={credText}
          placeholder="email----password----clientId----refresh_token"
          onInput={(e) => setCredText(e.currentTarget.value)}
        />
        <label class="inline">
          mode{" "}
          <select
            value={credMode}
            onChange={(e) => setCredMode(e.currentTarget.value)}
          >
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
          只测 refresh_token 是否还能换 access_token；成功=好用。仅
          grant_expired / refresh_invalid / abuse_mode 默认可移出；网络超时标为「不定」不默认勾选。
          不会返回密码/token。探测成功若 token 旋转会回写主池。
        </p>

        <div class="mail-probe-kpis">
          <span>
            主池 <strong>{pool ? pool.total : "—"}</strong>
          </span>
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
            开始探测
          </Button>
          <Button variant="ghost" onClick={loadPool} disabled={busy === "probe"}>
            刷新统计
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
                    const rowClass =
                      r.status === "ok" ? "ok" : q ? "dead" : "soft";
                    const badgeClass =
                      r.status === "ok"
                        ? "badge ok"
                        : q
                          ? "badge bad"
                          : "badge soft";
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
