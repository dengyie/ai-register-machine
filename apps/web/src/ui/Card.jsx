export function Card({ children, class: cls = "", ...rest }) {
  return (
    <div class={`card ${cls}`} {...rest}>
      {children}
    </div>
  );
}
