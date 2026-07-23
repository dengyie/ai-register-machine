export function StatusDot({ kind = "idle", class: cls = "" }) {
  // kind: ok | warn | err | busy | idle
  return <span class={`status-dot status-${kind} ${cls}`} aria-hidden="true" />;
}
