export function Tabs({ items, value, onChange }) {
  // items: [{ id, label }]
  return (
    <div class="tabrow" role="tablist">
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          role="tab"
          class={`tab ${value === it.id ? "active" : ""}`}
          aria-selected={value === it.id}
          onClick={() => onChange(it.id)}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
