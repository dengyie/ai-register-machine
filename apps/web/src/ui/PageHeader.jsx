// Shared page header: title + hint on the left, toolbar slot on the right.
// toolbarClass lets LogsPage keep its `toolbar wrap` variant verbatim.
export function PageHeader({ title, hint, children, toolbarClass = "toolbar" }) {
  return (
    <header class="page-head">
      <div>
        <h1>{title}</h1>
        {hint ? <p class="hint">{hint}</p> : null}
      </div>
      {children ? <div class={toolbarClass}>{children}</div> : null}
    </header>
  );
}
