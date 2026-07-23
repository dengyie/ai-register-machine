// src/store/feedback.js
import { signal } from "@preact/signals";

const OPS_LOG_MAX = 40;
export const opsLog = signal([]);
export const stickyBanner = signal(null); // { message, kind } | null
export const toasts = signal([]); // { id, message, kind }[]

let toastSeq = 0;

export function pushOpsLog(message, kind = "info") {
  const t = new Date().toTimeString().slice(0, 8);
  const next = [{ t, kind, m: String(message || "") }, ...opsLog.value];
  if (next.length > OPS_LOG_MAX) next.length = OPS_LOG_MAX;
  opsLog.value = next;
}

export function showOpsFeedback(
  message,
  kind = "info",
  { toast = true, sticky = true, log = true } = {},
) {
  const text = String(message || "").trim() || "(无消息)";
  if (sticky) stickyBanner.value = { message: text, kind };
  if (log) pushOpsLog(text, kind);
  if (!toast) return;
  const id = ++toastSeq;
  toasts.value = [
    ...toasts.value,
    { id, message: text.length > 220 ? text.slice(0, 217) + "…" : text, kind },
  ];
  const ms = kind === "err" ? 6500 : 3200;
  setTimeout(() => {
    toasts.value = toasts.value.filter((x) => x.id !== id);
  }, ms);
}

export function clearStickyBanner() {
  stickyBanner.value = null;
}
