// MailPoolProbe — 邮箱池 KPI + OAuth 刷新探测 + 精简去重 + 移出。
// Moved verbatim from MailTab.jsx — JSX/classes/copy unchanged.
import { useEffect, useMemo, useState } from "preact/hooks";
import * as api from "../../../api/client.js";
import { showOpsFeedback } from "../../../store/feedback.js";
import { Button } from "../../../ui/index.js";
import { formatApiError } from "../../../lib/format.js";
import { pretty, auth401 } from "../../../lib/http.js";
import { useBusy } from "../../../lib/useBusy.js";

export function isQuarantinable(row) {
  if (!row) return false;
  if (typeof row.quarantinable === "boolean") return row.quarantinable;
  return (
    row.status === "grant_expired" ||
    row.status === "refresh_invalid" ||
    row.status === "abuse_mode"
  );
}

export function statusLabel(row) {
  if (!row) return "—";
  if (row.status === "ok") return "好用";
  if (isQuarantinable(row)) return "挂了";
  return "不定";
}

export function MailPoolProbe({ pool, onReloadPool, onResult }) {
  const [probeDomains, setProbeDomains] = useState([]);
  const [probeLimit, setProbeLimit] = useState(30);
  const [probeOut, setProbeOut] = useState(null);
  const [selected, setSelected] = useState({}); // email -> bool
  const { setBusy, is } = useBusy();

  // Seed the domain selection from whatever the pool reports (parent owns the
  // fetch); never clobber a selection the user already made.
  useEffect(() => {
    if (!pool) return;
    const known = pool.known_domains || [];
    const present = Object.keys(pool.by_domain || {});
    const defaults = known.filter((d) => present.includes(d));
    setProbeDomains((prev) =>
      prev.length ? prev : defaults.length ? defaults : known.slice(0, 2),
    );
  }, [pool]);

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
      onResult(
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
      if (auth401(e)) return;
      showOpsFeedback(`探测失败: ${formatApiError(e)}`, "err");
      onResult(pretty({ error: formatApiError(e) }));
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
      onResult(pretty(data));
      const summary =
        data.summary ||
        `${dryRun ? "预览" : "精简"} · 唯一 ${data.unique ?? "—"} · 重复 ${data.duplicate_extra ?? 0}`;
      showOpsFeedback(summary, data.changed ? "ok" : "info", {
        toast: true,
        sticky: true,
      });
      await onReloadPool();
    } catch (e) {
      if (auth401(e)) return;
      showOpsFeedback(`精简失败: ${formatApiError(e)}`, "err");
      onResult(pretty({ error: formatApiError(e) }));
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
      onResult(pretty(data));
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
      await onReloadPool();
    } catch (e) {
      if (auth401(e)) return;
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
                disabled={is("probe")}
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
            disabled={is("probe")}
            onInput={(e) => setProbeLimit(e.currentTarget.value)}
          />
        </label>
        <Button
          variant="primary"
          busy={is("probe")}
          onClick={runProbe}
          disabled={is("probe") || !probeDomains.length}
        >
          开始探测（验证可用性）
        </Button>
        <Button variant="ghost" onClick={onReloadPool} disabled={is("probe")}>
          刷新统计
        </Button>
        <Button
          variant="ghost"
          busy={is("compact")}
          onClick={() => doCompact(true)}
          disabled={is("compact") || is("probe")}
        >
          预览精简
        </Button>
        <Button
          variant="ghost"
          busy={is("compact")}
          onClick={() => doCompact(false)}
          disabled={
            is("compact") ||
            is("probe") ||
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
              busy={is("quarantine")}
              onClick={doQuarantine}
              disabled={!selectedEmails.length || is("quarantine")}
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
  );
}
