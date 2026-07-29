// src/pages/Register/RunProgress.jsx
// Right panel: live run progress. Renders from run store signals.
// Pure render pieces come from progressRender.jsx.
import { useState } from "preact/hooks";
import { StatusDot, Chip, Kpi } from "../../ui/index.js";
import {
  currentRunState,
  overviewState,
  lastProductOk,
} from "../../store/run.js";
import {
  runHeader,
  kpiGrid,
  bars,
  stepRail,
  statusCard,
  recentWrites,
  timeline,
  failureStats,
} from "./progressRender.jsx";
import "../../styles/run-progress.css";

function barKind(cls) {
  if (cls === "danger") return "err";
  if (cls === "warn") return "warn";
  return "ok";
}

export function RunProgress({ onGotoLogs }) {
  const [timelineOpen, setTimelineOpen] = useState(true);
  const [writesOpen, setWritesOpen] = useState(true);
  const run = currentRunState.value;
  const ov = overviewState.value;

  const head = runHeader(run);
  const kpis = kpiGrid(run, ov, lastProductOk.value);
  const brs = bars(run);
  const steps = stepRail(run && run.steps);
  const sc = statusCard(run);
  const writes = recentWrites(run && run.recent_writes);
  const tl = timeline(run && run.timeline);
  const fstats = failureStats(run);

  return (
    <div class="run-progress">
      <div
        class={`run-header run-header-${head.state}`}
        data-state={head.state}
      >
        <StatusDot kind={head.dotKind} />
        <span class="status-word">{head.word}</span>
        <span class="meta-chips">
          {head.chips.map((c, i) => (
            <Chip
              key={i}
              kind={c.danger ? "err" : "default"}
              class="mini"
              title={c.title}
            >
              {c.label}
            </Chip>
          ))}
        </span>
      </div>

      <div class="kpi-grid">
        {kpis.map((k, i) => (
          <Kpi
            key={i}
            label={k.label}
            value={k.value}
            hint={k.hint}
            class={k.cls}
          />
        ))}
      </div>

      <div class="bars">
        {brs.map((b, i) => {
          const p = b.value == null ? 0 : b.value;
          const cap = b.value == null
            ? "—"
            : `${fmtN(b.a)} / ${fmtN(b.b)} (${Math.round(b.value)}%)`;
          return (
            <div key={i} class="bar-row">
              <span class="bar-label">{b.label}</span>
              <div
                class="bar-track"
                role="progressbar"
                aria-valuenow={Math.round(p)}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div
                  class={`bar-fill bar-${barKind(b.cls)}`}
                  style={{ width: `${p.toFixed(1)}%` }}
                />
              </div>
              <span class={`bar-caption ${b.cls || ""}`}>{cap}</span>
            </div>
          );
        })}
      </div>

      {steps.length > 0 ? (
        <div class="step-rail">
          {steps.map((s, i) => (
            <span
              key={i}
              class={`step ${s.state}`}
              title={s.desc}
            >
              {s.title}
            </span>
          ))}
        </div>
      ) : null}

      <div class="status-card">
        <div class="status-title">{sc.title}</div>
        <pre class="status-body">{sc.body}</pre>
      </div>

      {fstats ? <FailureStats stats={fstats} /> : null}

      {writes.length > 0 ? (
        <details
          class="writes-wrap"
          open={writesOpen}
          onToggle={(e) => setWritesOpen(e.currentTarget.open)}
        >
          <summary>最近落盘 ({writes.length})</summary>
          <div class="run-writes">
            {writes.map((w, i) => (
              <span key={i} class="write-chip" title={w.raw}>
                {w.name}
              </span>
            ))}
          </div>
        </details>
      ) : null}

      <details
        class="timeline-wrap"
        open={timelineOpen}
        onToggle={(e) => setTimelineOpen(e.currentTarget.open)}
      >
        <summary>时间线</summary>
        <ol class="timeline">
          {tl.length ? (
            tl.map((it, i) => (
              <li key={i} class="timeline-item">
                <span class="src">{it.src}</span>
                <span>
                  {it.title}
                  {it.line ? ` · ${it.line}` : ""}
                </span>
              </li>
            ))
          ) : (
            <li class="timeline-item hint">暂无事件</li>
          )}
        </ol>
      </details>

      <p class="hint progress-log-hint">
        worker / supervisor 完整 tail 与历史 →
        <button
          type="button"
          class="linkish"
          onClick={onGotoLogs}
        >
          日志
        </button>
      </p>
    </div>
  );
}

function fmtN(v) {
  return v == null || v === "" ? "—" : String(v);
}

// Batch failure statistics card. Renders only when run.batch_failures has data.
function FailureStats({ stats }) {
  const { kpis, breakdown, reasons, series } = stats;
  return (
    <div class="failure-stats">
      <div class="failure-stats-head">
        <span class="failure-stats-title">批次失败统计</span>
        <span class="hint">{stats.subs} 个子批聚合</span>
      </div>

      <div class="failure-kpis">
        {kpis.map((k, i) => (
          <div key={i} class={`failure-kpi ${k.cls || ""}`}>
            <div class="failure-kpi-value">{k.value}</div>
            <div class="failure-kpi-label">{k.label}</div>
            {k.hint ? <div class="failure-kpi-hint hint">{k.hint}</div> : null}
          </div>
        ))}
      </div>

      {breakdown.length > 0 ? (
        <div class="failure-breakdown">
          <div class="failure-breakdown-bar">
            {breakdown.map((s, i) => (
              <div
                key={i}
                class={`fseg fseg-${s.cls}`}
                style={{ width: `${s.pct.toFixed(1)}%` }}
                title={`${s.label}: ${s.value}`}
              />
            ))}
          </div>
          <div class="failure-legend">
            {breakdown.map((s, i) => (
              <span key={i} class="failure-legend-item">
                <span class={`fdot fseg-${s.cls}`} />
                {s.label} <b>{s.value}</b>
              </span>
            ))}
          </div>
        </div>
      ) : (
        <p class="hint failure-empty">本批暂无失败记录 ✓</p>
      )}

      {series.fail.length > 1 ? (
        <FailureSparkline fail={series.fail} success={series.success} />
      ) : null}

      {reasons.map((r, i) => (
        <div key={i} class="failure-reasons">
          <div class="failure-reasons-title">{r.title}</div>
          {r.rows.map((row, j) => (
            <div key={j} class="failure-reason-row">
              <span class="failure-reason-label" title={row.label}>
                {row.label}
              </span>
              <span class="failure-reason-count">{row.count}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

// Compact per-sub reg fail/success trend. Pure SVG, no chart lib.
function FailureSparkline({ fail, success }) {
  const n = fail.length;
  const totals = fail.map((f, i) => f + (success[i] || 0));
  const max = Math.max(1, ...totals);
  const W = 100;
  const H = 28;
  const bw = W / n;
  return (
    <div class="failure-spark">
      <div class="failure-spark-title hint">每子批 失败/成功 趋势</div>
      <svg viewBox={`0 0 ${W} ${H}`} class="failure-spark-svg" preserveAspectRatio="none">
        {fail.map((f, i) => {
          const ok = success[i] || 0;
          const total = f + ok;
          const x = i * bw;
          const failH = (f / max) * H;
          const okH = (ok / max) * H;
          return (
            <g key={i}>
              <rect
                x={x + bw * 0.15}
                y={H - okH}
                width={bw * 0.7}
                height={okH}
                class="spark-ok"
              />
              <rect
                x={x + bw * 0.15}
                y={H - okH - failH}
                width={bw * 0.7}
                height={failH}
                class="spark-fail"
              >
                <title>{`sub#${i + 1}: fail=${f} ok=${ok}`}</title>
              </rect>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
