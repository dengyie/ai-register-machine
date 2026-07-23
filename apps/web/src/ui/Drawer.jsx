export function Drawer({ open, title, onClose, children, class: cls = "" }) {
  if (!open) return null;
  return (
    <div class="drawer-backdrop" onClick={onClose}>
      <div
        class={`drawer ${cls}`}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div class="drawer-head">
          <div class="drawer-title">{title}</div>
          <button type="button" class="btn btn-ghost btn-sm" onClick={onClose}>
            关闭
          </button>
        </div>
        <div class="drawer-body">{children}</div>
      </div>
    </div>
  );
}
