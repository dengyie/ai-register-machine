export function Field({ label, span2 = false, children, class: cls = "" }) {
  return (
    <label class={`field ${span2 ? "span2" : ""} ${cls}`}>
      {label ? <span class="field-label">{label}</span> : null}
      {children}
    </label>
  );
}
