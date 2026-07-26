// Clash leaf filtering / pool-name resolution + the NodesTab select options.
// Moved verbatim from NodesTab.jsx — behavior unchanged.

export function filterClashLeaves(leaves, mode, q) {
  let out = leaves || [];
  if (mode === "pool") out = out.filter((n) => n.in_register_pool);
  else if (mode === "ok") out = out.filter((n) => n.health === "ok");
  else if (mode === "fail") out = out.filter((n) => n.health === "fail");
  const qq = (q || "").trim().toLowerCase();
  if (qq) out = out.filter((n) => (n.name || "").toLowerCase().includes(qq));
  return out.slice().sort((a, b) => {
    const pa = a.in_register_pool ? 0 : 1;
    const pb = b.in_register_pool ? 0 : 1;
    if (pa !== pb) return pa - pb;
    const rank = { ok: 0, unknown: 1, fail: 2 };
    const ha = rank[a.health || "unknown"] ?? 1;
    const hb = rank[b.health || "unknown"] ?? 1;
    if (ha !== hb) return ha - hb;
    const da = a.last_delay_ms != null ? a.last_delay_ms : 1e9;
    const db = b.last_delay_ms != null ? b.last_delay_ms : 1e9;
    return da - db;
  });
}

export function namesInPool(listing, groupName, limit = 120) {
  const g = (groupName || "").trim();
  const leaves = (listing && listing.leaves) || [];
  let names = leaves
    .filter((x) => Array.isArray(x.groups) && x.groups.includes(g))
    .map((x) => x.name);
  // fallback: register-pool flag when group is a known register group
  if (!names.length && (g.includes("Grok") || g.includes("GROK") || g === "PROXY")) {
    names = leaves.filter((x) => x.in_register_pool).map((x) => x.name);
  }
  return names.slice(0, Math.max(1, limit));
}

export const CLASH_FILTER = [
  { value: "pool", label: "仅注册池" },
  { value: "all", label: "全部叶子" },
  { value: "ok", label: "仅可用" },
  { value: "fail", label: "仅失败" },
];

// Must match apps.control_api.nodes_ops.IMPORTABLE_GROUPS (register-relevant first).
export const IMPORT_POOL_OPTS = [
  { value: "🎯Grok注册", label: "🎯Grok注册 · 注册主池（推荐）" },
  { value: "♻️Grok优选", label: "♻️Grok优选" },
  { value: "PROXY", label: "PROXY" },
  { value: "🔰ChatGPT", label: "🔰ChatGPT" },
  { value: "GROK-REG", label: "GROK-REG · 域名隔离组" },
];

export const IMPORT_MODE_OPTS = [
  { value: "merge", label: "merge · 追加/同名覆盖" },
  { value: "replace_prefix", label: "replace_prefix · 替换同前缀" },
];

export const HEALTH_OPTS = [
  { value: "", label: "全部" },
  { value: "ok", label: "可用" },
  { value: "fail", label: "失败" },
  { value: "unknown", label: "未测" },
];

export const TIER_OPTS = [
  { value: "", label: "全部" },
  { value: "0", label: "机房 0" },
  { value: "1", label: "住宅 1" },
];

export const PAGE_SIZES = [
  { value: "25", label: "25" },
  { value: "50", label: "50" },
  { value: "100", label: "100" },
];
