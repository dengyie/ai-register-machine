export function Select({ options = [], value, onChange, class: cls = "", ...rest }) {
  // options: [{ value, label }] or string[]
  return (
    <select
      class={`select ${cls}`}
      value={value}
      onChange={(e) => onChange && onChange(e.currentTarget.value)}
      {...rest}
    >
      {options.map((o) =>
        typeof o === "string" ? (
          <option key={o} value={o}>
            {o}
          </option>
        ) : (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ),
      )}
    </select>
  );
}
