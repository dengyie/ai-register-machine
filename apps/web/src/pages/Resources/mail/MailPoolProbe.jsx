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

const WAVE_LIMIT_MAX = 500;
// Full-scan UI keeps at most this many result rows in the table (oldest dropped)
// so a 64k pool does not freeze the browser. Counts still aggregate fully.
const FULL_SCAN_RESULTS_CAP = 2000;
// Cloudflare origin ~100s → keep UI waves under gateway budget; more waves OK.
const FULL_SCAN_WALL_SECONDS = 90;
const FULL_SCAN_DEFAULT_WAVE = 200;

function mergeByStatus(into, add) {
  const out = { ...(into || {}) };
  for (const [k, v] of Object.entries(add || {})) {
    out[k] = (out[k] || 0) + Number(v || 0);
  }
  return out;
}

function capResults(rows, cap) {
  if (!rows || rows.length <= cap) return rows || [];
  return rows.slice(rows.length - cap);
}

function collectQuarantinableEmails(results, into) {
  const out = into instanceof Set ? into : new Set(into || []);
  for (const r of results || []) {
    if (isQuarantinable(r) && r.email) out.add(String(r.email));
  }
  return out;
}

export function MailPoolProbe({ pool, onReloadPool, onResult }) {
  const [probeDomains, setProbeDomains] = useState([]);
  const [probeLimit, setProbeLimit] = useState(30);
  const [probeOut, setProbeOut] = useState(null);
  const [selected, setSelected] = useState({}); // email -> bool
  // Uncapped dead-email set across full-scan waves (table is capped).
  const [quarantinableEmails, setQuarantinableEmails] = useState([]);
  const [scanProgress, setScanProgress] = useState(null); // {offset, total, wave, ...}
  const { setBusy, is, any } = useBusy();
  // Abort flag for multi-wave full scan (mutable ref via object so closures see it).
  const scanCtl = useMemo(() => ({ stop: false }), []);

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

  function selectFromEmailList(emails) {
    const nextSel = {};
    for (const e of emails || []) {
      if (e) nextSel[e] = true;
    }
    setSelected(nextSel);
  }

  async function runProbeOnce({ offset = null, limit, concurrency, wallSeconds } = {}) {
    const body = {
      domains: probeDomains,
      limit: Math.max(1, Math.min(WAVE_LIMIT_MAX, Number(limit ?? probeLimit) || 30)),
      concurrency: concurrency ?? 4,
      wall_seconds: wallSeconds ?? 90,
    };
    if (offset != null) body.offset = offset;
    return api.probeMail(body);
  }

  async function runProbe() {
    if (!probeDomains.length) {
      showOpsFeedback("请先勾选至少一个域名（全不选不会测全部）", "info");
      return;
    }
    if (any) return;
    scanCtl.stop = false;
    setBusy("probe");
    setScanProgress(null);
    try {
      const data = await runProbeOnce({
        limit: probeLimit,
        concurrency: 4,
        wallSeconds: 90,
      });
      const qEmails = [...collectQuarantinableEmails(data.results)];
      setQuarantinableEmails(qEmails);
      setProbeOut(data);
      selectFromEmailList(qEmails);
      const qn =
        data.quarantinable != null
          ? data.quarantinable
          : qEmails.length;
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
          mode: data.mode,
        }),
      );
    } catch (e) {
      if (auth401(e)) return;
      showOpsFeedback(`探测失败: ${formatApiError(e)}`, "err");
      onResult(pretty({ error: formatApiError(e) }));
    } finally {
      setBusy("");
      setScanProgress(null);
    }
  }

  async function runFullScan() {
    if (!probeDomains.length) {
      showOpsFeedback("请先勾选至少一个域名（全不选不会测全部）", "info");
      return;
    }
    if (any) return;
    const totalHint =
      probeDomains.reduce((s, d) => s + Number((pool && pool.by_domain && pool.by_domain[d]) || 0), 0) ||
      (pool && pool.total) ||
      0;
    // Prefer explicit per-wave input; if still default sample size (30), use a
    // larger full-scan default so 64k does not need ~2k waves.
    const rawLimit = Number(probeLimit);
    const waveLimit = Math.max(
      1,
      Math.min(
        WAVE_LIMIT_MAX,
        Number.isFinite(rawLimit) && rawLimit > 0
          ? rawLimit === 30
            ? FULL_SCAN_DEFAULT_WAVE
            : rawLimit
          : FULL_SCAN_DEFAULT_WAVE,
      ),
    );
    const ok = window.confirm(
      `确认扫完全池？\n\n` +
        `· 域名: ${probeDomains.join(", ")}\n` +
        `· 约 ${totalHint || "?"} 条，按每波 ${waveLimit} 顺序扫（wall ${FULL_SCAN_WALL_SECONDS}s，避开 CF 524）\n` +
        `· 扫完前不可移出/精简（避免 offset 错位）；可点「停止全扫」\n` +
        `· 表格最多保留最近 ${FULL_SCAN_RESULTS_CAP} 行；可移邮箱名单全量累计`,
    );
    if (!ok) return;

    scanCtl.stop = false;
    setBusy("probe");
    setQuarantinableEmails([]);
    // Higher concurrency; wall capped under CF ~100s origin budget.
    const concurrency = 8;
    const wallSeconds = FULL_SCAN_WALL_SECONDS;
    let offset = 0;
    let wave = 0;
    let totalOk = 0;
    let totalDead = 0;
    let totalQ = 0;
    let totalProbed = 0;
    let byStatus = {};
    let timedAny = false;
    let allResults = [];
    let qEmailSet = new Set();
    let poolTotal = totalHint;
    let stopped = false;

    try {
      while (!scanCtl.stop) {
        wave += 1;
        setScanProgress({
          wave,
          offset,
          total: poolTotal,
          probed: totalProbed,
          ok: totalOk,
          dead: totalDead,
          quarantinable: totalQ,
        });
        const data = await runProbeOnce({
          offset,
          limit: waveLimit,
          concurrency,
          wallSeconds,
        });
        if (data.pool_filtered_total != null) poolTotal = data.pool_filtered_total;
        totalProbed += Number(data.probed || 0);
        totalOk += Number(data.ok || 0);
        totalDead += Number(data.dead || 0);
        totalQ += Number(data.quarantinable || 0);
        timedAny = timedAny || !!data.timed_out;
        byStatus = mergeByStatus(byStatus, data.by_status);
        qEmailSet = collectQuarantinableEmails(data.results, qEmailSet);
        allResults = capResults(
          allResults.concat(data.results || []),
          FULL_SCAN_RESULTS_CAP,
        );
        const qEmailList = [...qEmailSet];
        const agg = {
          mode: "sequential_all",
          probed: totalProbed,
          ok: totalOk,
          dead: totalDead,
          quarantinable: totalQ,
          by_status: byStatus,
          timed_out: timedAny,
          results: allResults,
          results_capped: allResults.length < totalProbed,
          pool_filtered_total: poolTotal,
          offset: data.offset,
          next_offset: data.next_offset,
          done: !!data.done,
          wave,
        };
        setProbeOut(agg);
        setQuarantinableEmails(qEmailList);
        // Don't auto-select thousands mid-scan (selection is heavy); user hits 全选可移出 after.
        setScanProgress({
          wave,
          offset: data.offset,
          next_offset: data.next_offset,
          total: poolTotal,
          probed: totalProbed,
          ok: totalOk,
          dead: totalDead,
          quarantinable: totalQ,
          done: !!data.done,
        });

        if (data.done || !data.probed) break;
        const nxt = data.next_offset;
        if (nxt == null || Number(nxt) <= offset) break;
        offset = Number(nxt);
      }
      if (scanCtl.stop) stopped = true;

      const qEmailList = [...qEmailSet];
      setQuarantinableEmails(qEmailList);
      selectFromEmailList(qEmailList);

      const msg =
        `${stopped ? "全扫已停止" : "全扫完成"}: 探测 ${totalProbed}/${poolTotal || "?"} · 好用 ${totalOk} · 可移 ${totalQ} · 不定 ${Math.max(0, totalDead - totalQ)}` +
        (timedAny ? " · 含超时截断波" : "");
      showOpsFeedback(msg, totalDead ? "info" : "ok", { toast: true, sticky: true });
      onResult(
        pretty({
          mode: "sequential_all",
          stopped,
          probed: totalProbed,
          ok: totalOk,
          dead: totalDead,
          quarantinable: totalQ,
          pool_filtered_total: poolTotal,
          waves: wave,
          timed_out: timedAny,
          by_status: byStatus,
          results_shown: allResults.length,
          quarantinable_emails: qEmailList.length,
        }),
      );
      await onReloadPool();
    } catch (e) {
      if (auth401(e)) return;
      showOpsFeedback(`全扫失败: ${formatApiError(e)}`, "err");
      onResult(pretty({ error: formatApiError(e) }));
    } finally {
      setBusy("");
      setScanProgress(null);
    }
  }

  function stopFullScan() {
    scanCtl.stop = true;
    showOpsFeedback("正在停止全扫（当前波结束后停下）…", "info");
  }

  function selectAllDead() {
    // Prefer uncapped full-scan email list; fall back to visible table rows.
    if (quarantinableEmails && quarantinableEmails.length) {
      selectFromEmailList(quarantinableEmails);
      return;
    }
    const next = {};
    for (const r of (probeOut && probeOut.results) || []) {
      if (isQuarantinable(r) && r.email) next[r.email] = true;
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

  // Full scan mutates offsets against a frozen pool — block quarantine/compact
  // while probe busy so single-slot useBusy cannot clear "probe" mid-loop.
  const poolMutationsBlocked = is("probe");

  async function doCompact(dryRun = false) {
    if (poolMutationsBlocked) {
      showOpsFeedback("探测/全扫进行中，请先停止后再精简", "info");
      return;
    }
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
    if (poolMutationsBlocked) {
      showOpsFeedback("全扫/探测进行中，请先停止后再移出（避免 offset 错位）", "info");
      return;
    }
    if (!selectedEmails.length) {
      showOpsFeedback("请先勾选要移出的邮箱", "info");
      return;
    }
    const rows = (probeOut && probeOut.results) || [];
    const byEmail = Object.fromEntries(
      rows.map((r) => [String(r.email || "").toLowerCase(), r]),
    );
    // Uncapped full-scan list is authoritative for hard-dead even when table capped.
    const qSet = new Set(
      (quarantinableEmails || []).map((e) => String(e).toLowerCase()),
    );
    const hard = [];
    const soft = [];
    for (const e of selectedEmails) {
      const low = e.toLowerCase();
      const r = byEmail[low];
      if ((r && isQuarantinable(r)) || qSet.has(low)) hard.push(e);
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
      const removedN = Number(data.removed || 0);
      showOpsFeedback(
        `已移出 ${removedN} 个（未找到 ${data.not_found || 0}）`,
        "ok",
      );
      onResult(pretty(data));
      const gone = new Set(hard.map((e) => e.toLowerCase()));
      setQuarantinableEmails((prev) =>
        (prev || []).filter((e) => !gone.has(String(e).toLowerCase())),
      );
      if (probeOut) {
        const left = (probeOut.results || []).filter(
          (r) => !gone.has(String(r.email || "").toLowerCase()),
        );
        // Decrement aggregates by removed delta — never recompute from capped table.
        const prevQ =
          probeOut.quarantinable != null
            ? Number(probeOut.quarantinable)
            : (probeOut.results || []).filter(isQuarantinable).length;
        setProbeOut({
          ...probeOut,
          results: left,
          // keep probed/ok totals; dead/quarantinable shrink by successful removes
          dead: Math.max(0, Number(probeOut.dead || 0) - removedN),
          quarantinable: Math.max(0, prevQ - removedN),
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
        单次探测最多 {WAVE_LIMIT_MAX}/波；大池请用「扫完全池」按 offset 顺序多波扫完。
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
          每波{" "}
          <input
            type="number"
            min={1}
            max={WAVE_LIMIT_MAX}
            value={probeLimit}
            style={{ width: "5rem" }}
            disabled={is("probe")}
            onInput={(e) => setProbeLimit(e.currentTarget.value)}
            title={`单波/抽样上限 ${WAVE_LIMIT_MAX}；全扫时作为每波大小`}
          />
        </label>
        <Button
          variant="primary"
          busy={is("probe") && !scanProgress}
          onClick={runProbe}
          disabled={is("probe") || !probeDomains.length}
        >
          开始探测（抽样）
        </Button>
        <Button
          variant="primary"
          busy={is("probe") && !!scanProgress}
          onClick={runFullScan}
          disabled={is("probe") || !probeDomains.length}
          title="按文件顺序 offset 分页扫完全部已选域名"
        >
          扫完全池
        </Button>
        {is("probe") && scanProgress ? (
          <Button variant="danger" onClick={stopFullScan}>
            停止全扫
          </Button>
        ) : null}
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

      {scanProgress ? (
        <p class="hint mail-probe-progress">
          全扫进行中 · 第 {scanProgress.wave} 波 · offset {scanProgress.offset}
          {scanProgress.next_offset != null ? ` → ${scanProgress.next_offset}` : ""}
          {" · "}
          已测 {scanProgress.probed}
          {scanProgress.total ? ` / ${scanProgress.total}` : ""}
          {" · 好用 "}
          {scanProgress.ok}
          {" · 可移 "}
          {scanProgress.quarantinable}
        </p>
      ) : null}

      {probeOut ? (
        <div class="mail-probe-results">
          <div class="toolbar wrap">
            <span>
              结果: 探测 {probeOut.probed}
              {probeOut.pool_filtered_total != null
                ? ` / ${probeOut.pool_filtered_total}`
                : ""}
              {" · 好用 "}
              {probeOut.ok} · 可移出{" "}
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
              {probeOut.mode === "sequential_all" || probeOut.mode === "sequential"
                ? ` · ${probeOut.mode}`
                : ""}
              {probeOut.results_capped
                ? ` · 表仅显示最近 ${(probeOut.results || []).length} 行`
                : ""}
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
              disabled={
                !selectedEmails.length ||
                is("quarantine") ||
                poolMutationsBlocked
              }
              title={
                poolMutationsBlocked
                  ? "探测/全扫进行中不可移出（避免 offset 错位）"
                  : undefined
              }
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
