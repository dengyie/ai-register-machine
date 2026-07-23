export function Chip({ kind = "default", children, class: cls = "" }) {
  return <span class={`chip chip-${kind} ${cls}`}>{children}</span>;
}
