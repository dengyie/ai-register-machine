import { toasts } from "../store/feedback.js";

export function ToastHost() {
  return (
    <div class="ops-toast-host" aria-live="polite">
      {toasts.value.map((t) => (
        <div key={t.id} class={`ops-toast ${t.kind}`}>
          {t.message}
        </div>
      ))}
    </div>
  );
}
