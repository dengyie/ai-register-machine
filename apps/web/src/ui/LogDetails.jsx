// Collapsible raw-response log. Toast/banner carry the primary feedback;
// the raw JSON is demoted to expandable evidence (batch-2 IA decision).
export function LogDetails({ text, summary = "响应详情", compact = false }) {
  if (!text) return null;
  return (
    <details class="log-details">
      <summary>{summary}</summary>
      <pre class={compact ? "log compact" : "log"}>{text}</pre>
    </details>
  );
}
