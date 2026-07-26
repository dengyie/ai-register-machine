// ProviderPoolStatus — 当前 email_providers 池状态行（batch-2 IA）
// Replaces the hint 长段 on Settings + Register: 两页对 池 的写权限不对称，
// 用一行状态 + 徽标表达，而不是每页各写一段规则说明。
// Self-contained styles (components.css) — .badge lives in accounts.css and is
// not loaded on Settings / Register.
export function ProviderPoolStatus({
  selected = [],
  primaryProvider = "",
  canClear = false,
  hydrated = true,
  dirty = false,
}) {
  return (
    <div class="pool-status">
      <span class="pool-status-label">当前池</span>
      {selected.length ? (
        selected.map((p) => (
          <span key={p} class="pool-chip">
            {p}
          </span>
        ))
      ) : (
        <span class="pool-chip empty">
          空 · 单通道 {primaryProvider || "—"}
        </span>
      )}
      <span class={`pool-tag ${canClear ? "can-clear" : ""}`}>
        {canClear ? "此页可清空池" : "此页不可清池（去设置页）"}
      </span>
      {!hydrated ? (
        <span class="pool-tag warn">未加载</span>
      ) : dirty ? (
        <span class="pool-tag warn">未保存</span>
      ) : null}
    </div>
  );
}
