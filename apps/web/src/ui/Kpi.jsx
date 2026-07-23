export function Kpi({ label, value, hint, class: cls = "" }) {
  return (
    <div class={`kpi ${cls}`}>
      <div class="kpi-label">{label}</div>
      <div class="kpi-value">{value}</div>
      {hint ? <div class="kpi-hint hint">{hint}</div> : null}
    </div>
  );
}
