export function Bar({ label, value, max = 100, kind = "ok", class: cls = "" }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div class={`bar-row ${cls}`}>
      {label ? <div class="bar-label">{label}</div> : null}
      <div class="bar-track" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div class={`bar-fill bar-${kind}`} style={{ width: `${pct}%` }} />
      </div>
      <div class="bar-value">{pct}%</div>
    </div>
  );
}
