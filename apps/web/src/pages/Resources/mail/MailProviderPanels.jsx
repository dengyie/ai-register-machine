// MailProviderPanels — the six per-provider collapsible panels.
// Batch-2 IA: 全局 defaultDomains（含 Worker 域名勾选）已提升到
// DefaultDomainsCard，本组件只剩真正的 per-provider 字段。
export function MailProviderPanels({ form, set, openPanels, onTogglePanel }) {
  function panel(name, title, body) {
    const open = !!openPanels[name];
    return (
      <details
        class="card provider-panel"
        open={open}
        onToggle={(e) => {
          const next = e.currentTarget.open;
          onTogglePanel(name, next);
        }}
      >
        <summary class="provider-panel-summary" onClick={(e) => e.preventDefault()}>
          <button type="button" class="provider-panel-toggle" onClick={() => onTogglePanel(name)}>
            {open ? "▾" : "▸"} {title}
          </button>
        </summary>
        {open ? <div class="provider-panel-body grid mail-form">{body}</div> : null}
      </details>
    );
  }

  return (
    <>
      {panel(
        "cloudflare",
        "Cloudflare 临时邮",
        <>
          <label>
            cloudflare_api_base
            <input
              value={form.cloudflare_api_base}
              placeholder="https://mail-api.example.com"
              onInput={(e) => set({ cloudflare_api_base: e.currentTarget.value })}
            />
          </label>
          <label>
            cloudflare_api_key
            <input
              type="password"
              value={form.cloudflare_api_key}
              placeholder="leave empty to keep"
              onInput={(e) => set({ cloudflare_api_key: e.currentTarget.value })}
            />
          </label>
          <p class="span2 hint tight">
            域名勾选与 <code>defaultDomains</code> 在上方「全局域名池」统一编辑。
          </p>
        </>,
      )}

      {panel(
        "cloudmail",
        "CloudMail 自建",
        <>
          <label>
            cloudmail_url
            <input
              value={form.cloudmail_url}
              placeholder="https://mail.example.com"
              onInput={(e) => set({ cloudmail_url: e.currentTarget.value })}
            />
          </label>
          <label>
            cloudmail_admin_email
            <input
              value={form.cloudmail_admin_email}
              onInput={(e) => set({ cloudmail_admin_email: e.currentTarget.value })}
            />
          </label>
          <label>
            cloudmail_password
            <input
              type="password"
              value={form.cloudmail_password}
              placeholder="leave empty to keep"
              onInput={(e) => set({ cloudmail_password: e.currentTarget.value })}
            />
          </label>
          <p class="span2 hint tight">
            收件域名用上方「全局域名池」的 <code>defaultDomains</code>。
          </p>
        </>,
      )}

      {panel(
        "duckmail",
        "DuckMail",
        <>
          <label class="span2">
            duckmail_api_key
            <input
              type="password"
              value={form.duckmail_api_key}
              placeholder="leave empty to keep"
              onInput={(e) => set({ duckmail_api_key: e.currentTarget.value })}
            />
          </label>
          <p class="span2 hint tight">域名由 DuckMail API /domains 自动获取，无需填写 defaultDomains。</p>
        </>,
      )}

      {panel(
        "yyds",
        "yydsmail",
        <>
          <label class="span2">
            yyds_api_key
            <input
              type="password"
              value={form.yyds_api_key}
              placeholder="leave empty to keep"
              onInput={(e) => set({ yyds_api_key: e.currentTarget.value })}
            />
          </label>
          <p class="span2 hint tight">
            域名由 yyds API 拉取。jwt 若需要请写 .env 的 YYDS_JWT（不在此暴露）。
          </p>
        </>,
      )}

      {panel(
        "gmail",
        "Gmail catch-all（IMAP）",
        <>
          <p class="span2 hint">
            表单不写应用密码。在项目 <code>.env</code> 配置{" "}
            <code>GMAIL_IMAP_USER</code> / <code>GMAIL_IMAP_PASSWORD</code>
            ；catch-all 域名用上方全局 defaultDomains（CF/CloudMail 面板）。
          </p>
          <label>
            gmail_imap_user（可选提示）
            <input
              value={form.gmail_imap_user}
              placeholder="可选 · 也可只写 .env"
              onInput={(e) => set({ gmail_imap_user: e.currentTarget.value })}
            />
          </label>
        </>,
      )}

      {panel(
        "hotmail",
        "Hotmail / Outlook",
        <>
          <label>
            hotmail_accounts_file
            <input
              value={form.hotmail_accounts_file}
              placeholder="mail_credentials.txt"
              onInput={(e) => set({ hotmail_accounts_file: e.currentTarget.value })}
            />
          </label>
          <label class="check span2">
            <input
              type="checkbox"
              checked={!!form.hotmail_allow_plus_alias}
              onChange={(e) =>
                set({ hotmail_allow_plus_alias: e.currentTarget.checked })
              }
            />{" "}
            hotmail_allow_plus_alias（生产勿开）
          </label>
          <p class="span2 hint tight">
            凭证格式：<code>邮箱----密码----ClientID----refresh_token</code>
            。不使用 defaultDomains。下方可批量导入。
          </p>
        </>,
      )}
    </>
  );
}
