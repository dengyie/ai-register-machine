// Shared email-provider pool helpers (Register / Settings / RegForm).
// Keep in sync with runtime _EMAIL_PROVIDER_POOL_KNOWN in grok_register_ttk.py.

export const EMAIL_PROVIDERS = [
  "cloudflare",
  "cloudmail",
  "duckmail",
  "yyds",
  "gmail",
  "hotmail",
];

export const EMAIL_PROVIDER_STRATEGIES = ["round_robin", "random", "failover"];

export function normalizeProviderName(raw) {
  let p = String(raw || "")
    .trim()
    .toLowerCase();
  if (p === "outlook" || p === "outlookmail" || p === "microsoft") p = "hotmail";
  if (p === "google" || p === "googlemail") p = "gmail";
  return p;
}

/** Parse multi-select from array or comma/space string; dedupe; known only. */
export function normalizeProvidersList(raw) {
  const parts = Array.isArray(raw)
    ? raw
    : String(raw || "")
        .split(/[,，;\s]+/)
        .filter(Boolean);
  const out = [];
  const seen = new Set();
  for (const item of parts) {
    const p = normalizeProviderName(item);
    if (!p || !EMAIL_PROVIDERS.includes(p) || seen.has(p)) continue;
    seen.add(p);
    out.push(p);
  }
  return out;
}

/**
 * Resolve pool from config.email_providers, else single email_provider.
 * Console Register/Settings load multi-only (no singleton promote) so a Settings
 * clear-pool stays empty across Reload/Save. Use this helper only where
 * runtime-style "multi or singleton" display is intentional.
 * @param {object} c
 * @param {{ fallback?: string[] }} [opts]
 */
export function providersFromConfig(c, { fallback = [] } = {}) {
  const multi = normalizeProvidersList(c && c.email_providers);
  if (multi.length) return multi;
  const single = normalizeProviderName(c && c.email_provider);
  if (single && EMAIL_PROVIDERS.includes(single)) return [single];
  return Array.isArray(fallback) ? fallback.slice() : [];
}
