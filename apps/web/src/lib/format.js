export function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export function dash(v) {
  return v == null || v === "" ? "—" : String(v);
}

export function fmtNum(v) {
  return v == null || v === "" ? "—" : String(v);
}

export function pct(a, b) {
  if (b == null || a == null || Number(b) <= 0) return null;
  return Math.max(0, Math.min(100, (Number(a) / Number(b)) * 100));
}

/** Collapse Cloudflare / HTML error pages into a short operator-facing line. */
export function summarizeErrorText(raw, status) {
  const text = String(raw ?? "").trim();
  if (!text) return status ? `HTTP ${status}` : "unknown error";

  const looksHtml =
    /<!DOCTYPE\s+html/i.test(text) ||
    /<html[\s>]/i.test(text) ||
    /<\/html>/i.test(text) ||
    /<title>/i.test(text);
  const mCode =
    text.match(/Error code\s+(\d{3})/i) ||
    text.match(/\bError\s+(\d{3})\b/i) ||
    text.match(/\b(52[0-9]|50[0-9])\s*:\s*A timeout occurred/i);
  const title =
    (text.match(/<title[^>]*>([^<]+)<\/title>/i) || [])[1] ||
    (text.match(/A timeout occurred/i) ? "A timeout occurred" : "");
  const code = mCode
    ? Number(mCode[1])
    : status && Number(status) >= 400
      ? Number(status)
      : null;

  if (looksHtml || (code && code >= 500 && text.length > 180)) {
    if (code === 524 || /timeout occurred/i.test(text) || /524/.test(title)) {
      return (
        "网关超时 (Cloudflare 524)：源站处理过久未响应。" +
        "请缩小测活范围/前缀，或稍后重试（长任务已改为并行测活）。"
      );
    }
    if (code === 502 || code === 503 || code === 504) {
      return `网关错误 (HTTP ${code})：源站暂时不可用，请稍后重试。`;
    }
    if (code) {
      const shortTitle = String(title || "")
        .replace(/\s*\|\s*.*$/, "")
        .trim()
        .slice(0, 80);
      return shortTitle
        ? `HTTP ${code}: ${shortTitle}`
        : `HTTP ${code}: 源站返回了错误页`;
    }
    return "源站返回了 HTML 错误页（已省略正文）";
  }

  // strip accidental HTML tags if a short fragment leaked
  let one = text.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  if (one.length > 240) one = `${one.slice(0, 220)}…`;
  return one;
}

export function formatApiError(e) {
  if (!e) return "unknown error";
  if (typeof e === "string") return summarizeErrorText(e);
  const status = e.status;
  const msg = e.message != null ? e.message : String(e);
  const summary = summarizeErrorText(msg, status);
  // summarizeErrorText already prefixes HTTP codes for HTML pages; avoid double "524: 524:"
  if (status && !/^HTTP\s+\d+/.test(summary) && !summary.includes(String(status))) {
    return `${status}: ${summary}`;
  }
  return summary;
}

export function healthBadge(h) {
  if (h === "ok" || h === true) return { label: "ok", cls: "ok" };
  if (h === "fail" || h === false) return { label: "fail", cls: "danger" };
  return { label: "?", cls: "muted" };
}
