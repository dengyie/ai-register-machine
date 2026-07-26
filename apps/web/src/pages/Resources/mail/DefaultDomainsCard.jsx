// DefaultDomainsCard — 全局 defaultDomains 一处编辑（batch-2 IA）
// 原来 CF 面板和 CloudMail 面板各有一个 defaultDomains 输入框，同一个值两处编辑，
// 看起来像 per-provider 配置。这里提升为 provider 面板之上的全局区，Worker 域名
// 勾选也一并放进来（勾选结果就是写进这个全局值）。
import { useMemo } from "preact/hooks";
import { Button } from "../../../ui/index.js";
import { parseDomainList } from "./domains.js";

export function DefaultDomainsCard({
  form,
  cfDomains,
  cfMeta,
  cfSelected,
  onFetchCfDomains,
  onToggleCfDomain,
  onEditDomains,
  busy,
}) {
  const selectedDomainSet = useMemo(
    () => new Set(cfSelected.map((d) => d.toLowerCase())),
    [cfSelected],
  );
  const known = cfDomains.length
    ? cfDomains
    : parseDomainList(form.defaultDomains);

  return (
    <div class="card mail-global-domains">
      <h2>全局域名池</h2>
      <p class="hint tight">
        Cloudflare / CloudMail / Gmail catch-all 共用这一份{" "}
        <code>defaultDomains</code>；yyds / duckmail 的域名由各自 API 拉取，不看这里。
      </p>

      <div class="toolbar wrap">
        <Button variant="ghost" busy={busy} onClick={onFetchCfDomains}>
          从 Worker 拉取域名
        </Button>
        <span class="hint tight">
          {cfMeta
            ? `已拉 ${cfMeta.count ?? cfDomains.length} · base=${cfMeta.api_base || "—"} · key=${cfMeta.has_api_key ? "有" : "无"} · 不自动全选`
            : "先保存 Cloudflare base/key 再拉；勾选写入 defaultDomains（不自动全选）"}
        </span>
      </div>

      <div class="mail-domain-chips" style={{ marginTop: "0.5rem" }}>
        {known.map((d) => (
          <label key={d} class="check chip">
            <input
              type="checkbox"
              checked={selectedDomainSet.has(String(d).toLowerCase())}
              onChange={() => onToggleCfDomain(d)}
            />{" "}
            {d}
          </label>
        ))}
        {!known.length ? (
          <span class="hint">尚无域名 · 拉取或手填下方</span>
        ) : null}
      </div>

      <label class="mail-domains-input">
        defaultDomains（全局 · CF 勾选结果 · 可清空）
        <input
          value={form.defaultDomains}
          placeholder="a.com,b.com · 留空保存即清空"
          onInput={(e) => onEditDomains(e.currentTarget.value)}
        />
      </label>
    </div>
  );
}
