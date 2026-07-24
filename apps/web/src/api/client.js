const TOKEN_KEY = "controlToken";

export function getToken() {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}
export function setToken(t) {
  if (t) sessionStorage.setItem(TOKEN_KEY, t);
  else sessionStorage.removeItem(TOKEN_KEY);
}
export function clearToken() {
  sessionStorage.removeItem(TOKEN_KEY);
}

export function headers(json = false) {
  const h = {};
  if (json) h["Content-Type"] = "application/json";
  const t = getToken();
  if (t) h["Authorization"] = `Bearer ${t}`;
  return h;
}

export async function api(path, opts = {}) {
  const callerHeaders = opts.headers || {};
  const hasAuth = Object.keys(callerHeaders).some(
    (k) => k.toLowerCase() === "authorization"
  );
  const token = getToken();
  const merged = {
    ...(token && !hasAuth ? { Authorization: `Bearer ${token}` } : {}),
    ...callerHeaders,
  };
  const res = await fetch(path, {
    credentials: "same-origin",
    ...opts,
    headers: merged,
  });
  const text = await res.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { detail: text };
  }
  if (!res.ok) {
    const detail = body.detail || body.error || body.message || res.statusText;
    let msg =
      typeof detail === "string"
        ? detail
        : detail != null
          ? JSON.stringify(detail)
          : res.statusText;
    // Never keep multi-KB Cloudflare HTML in Error.message — toast/banner explode.
    if (
      msg.length > 280 ||
      /<!DOCTYPE\s+html/i.test(msg) ||
      /<html[\s>]/i.test(msg)
    ) {
      const title = (msg.match(/<title[^>]*>([^<]+)<\/title>/i) || [])[1] || "";
      const codeM = msg.match(/Error code\s+(\d{3})/i);
      const code = codeM ? codeM[1] : String(res.status);
      if (String(code) === "524" || /timeout occurred/i.test(msg)) {
        msg = `网关超时 (Cloudflare 524)：源站处理过久未响应${title ? ` · ${title}` : ""}`;
      } else {
        msg = `HTTP ${code}: ${(title || "源站错误页").replace(/\s*\|\s*.*$/, "").trim()}`;
      }
    }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return body;
}

export async function postMultipart(url, formData) {
  const res = await fetch(url, {
    method: "POST",
    headers: headers(),
    body: formData,
    credentials: "same-origin",
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.detail || res.statusText);
    err.status = res.status;
    throw err;
  }
  return body;
}

// Auth
export const login = (username, password) =>
  api("/api/auth/login", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ username, password }),
  });
export const logout = () => api("/api/auth/logout", { method: "POST", headers: headers(true) });
export const me = () => api("/api/auth/me");

// Core
export const overview = () => api("/api/overview");
export const getConfig = () => api("/api/config");
export const putConfig = (partial) =>
  api("/api/config", {
    method: "PUT",
    headers: headers(true),
    body: JSON.stringify(partial),
  });

// Runs
export const listRuns = () => api("/api/runs");
export const currentRun = () => api("/api/runs/current");
export const startRun = (body) =>
  api("/api/runs/start", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
export const stopRun = () =>
  api("/api/runs/stop", { method: "POST", headers: headers(true) });
export const runLogs = (tail = 200, which = "auto") =>
  api(`/api/runs/current/logs?tail=${tail}&which=${which}`);

// Accounts
export const listAccounts = (qs) => api(`/api/accounts?${qs}`);
export const deleteAccount = (name) =>
  api(`/api/accounts/${encodeURIComponent(name)}`, {
    method: "DELETE",
    headers: headers(),
  });

// Nodes
export const listClash = () => api("/api/nodes/clash");
export const testClash = (body) =>
  api("/api/nodes/clash/test", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body || {}),
  });
export const importClashUrl = (body) =>
  api("/api/nodes/clash/import-url", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
export const deleteClashPrefix = (body) =>
  api("/api/nodes/clash/delete-prefix", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
export const pruneClashUnhealthy = (body) =>
  api("/api/nodes/clash/prune-unhealthy", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
export const listCatalog = (qs) => api(`/api/nodes?${qs}`);
export const addCatalogNode = (body) =>
  api("/api/nodes", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
export const testCatalog = (body) =>
  api("/api/nodes/test", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body || {}),
  });
export const deleteCatalogNode = (id) =>
  api(`/api/nodes/${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: headers(),
  });
export const patchCatalogNode = (id, body) =>
  api(`/api/nodes/${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify(body),
  });

// Import multipart
export const importNodesFile = (fd) => postMultipart("/api/import/nodes", fd);
export const importMailText = (fd) => postMultipart("/api/import/mail", fd);
export const importAuthsFile = (fd) => postMultipart("/api/import/auths", fd);
export const importPackFile = (fd) => postMultipart("/api/import/pack", fd);

// Ops
export const selfcheck = () => api("/api/ops/selfcheck", { headers: headers() });
export const cleanupOrphans = () =>
  api("/api/ops/cleanup-orphans?dry_run=false", {
    method: "POST",
    headers: headers(true),
  });

// Mail pool probe
export const mailPoolStats = () => api("/api/mail/pool");
export const probeMail = (body) =>
  api("/api/mail/probe", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body || {}),
  });
export const quarantineMail = (body) =>
  api("/api/mail/quarantine", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body || {}),
  });
