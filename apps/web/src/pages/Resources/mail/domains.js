// Domain-list helpers shared by MailTab and its sub-panels.
// Moved verbatim from MailTab.jsx — behavior unchanged.

export function parseDomainList(raw) {
  return String(raw || "")
    .replace(/，/g, ",")
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function joinDomains(list) {
  const out = [];
  const seen = new Set();
  for (const d of list || []) {
    const name = String(d || "").trim();
    if (!name) continue;
    const low = name.toLowerCase();
    if (seen.has(low)) continue;
    seen.add(low);
    out.push(name);
  }
  return out.join(",");
}
