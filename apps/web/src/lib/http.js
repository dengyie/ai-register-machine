// Shared API-call helpers: JSON pretty-printing + 401 session handling.
// Extracted from the four identical copies previously inlined in
// Settings / MailTab / NodesTab / ImportTab.
import { session } from "../store/session.js";

/** JSON.stringify with 2-space indent; strings pass through; never throws. */
export function pretty(v) {
  try {
    return typeof v === "string" ? v : JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

/** On a 401 error, drop the session so LoginGate shows. Returns true if handled. */
export function auth401(e) {
  if (e && e.status === 401) {
    session.value = { ...session.value, authenticated: false };
    return true;
  }
  return false;
}
