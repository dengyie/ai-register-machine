export function Button({
  variant = "ghost",
  size = "md",
  busy = false,
  type = "button",
  class: cls = "",
  children,
  ...rest
}) {
  return (
    <button
      type={type}
      class={`btn btn-${variant} btn-${size} ${busy ? "busy" : ""} ${cls}`}
      disabled={busy || rest.disabled}
      {...rest}
    >
      {busy ? "…" : children}
    </button>
  );
}
