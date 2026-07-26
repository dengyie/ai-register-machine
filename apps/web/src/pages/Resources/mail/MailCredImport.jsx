// MailCredImport — Hotmail / Outlook 凭证批量导入卡片。
// Moved verbatim from MailTab.jsx — JSX/classes/copy unchanged.
import { useState } from "preact/hooks";
import * as api from "../../../api/client.js";
import { showOpsFeedback } from "../../../store/feedback.js";
import { Button } from "../../../ui/index.js";
import { formatApiError } from "../../../lib/format.js";
import { pretty, auth401 } from "../../../lib/http.js";
import { useBusy } from "../../../lib/useBusy.js";

export function MailCredImport({ onResult, onImported }) {
  const [credText, setCredText] = useState("");
  const [credMode, setCredMode] = useState("append");
  const { setBusy, is } = useBusy();

  async function importCreds() {
    setBusy("cred");
    try {
      const fd = new FormData();
      fd.append("content", credText || "");
      fd.append("mode", credMode || "append");
      const body = await api.importMailText(fd);
      onResult(pretty(body));
      const r = (body && body.result) || body || {};
      const summary =
        (body && body.detail) ||
        r.summary ||
        `导入完成 · 新增 ${r.new ?? r.lines_written ?? 0} · 重复 ${r.duplicate ?? 0} · 无效 ${r.skipped ?? 0}`;
      const status = r.status || (r.new > 0 || r.lines_written > 0 ? "success" : "empty");
      const kind = status === "success" ? "ok" : status === "partial" ? "info" : "info";
      showOpsFeedback(summary, kind, { toast: true, sticky: true });
      await onImported();
    } catch (e) {
      if (auth401(e)) return;
      onResult(pretty({ error: formatApiError(e) }));
      showOpsFeedback(`导入失败: ${formatApiError(e)}`, "err");
    } finally {
      setBusy("");
    }
  }

  return (
    <div class="card">
      <h2>Hotmail / Outlook 凭证导入</h2>
      <p class="hint">
        支持四段 <code>email----password----clientId----token</code>、JSON / CSV /
        管道分隔；append 自动去重。导入结果会显示新增 / 重复 / 无效条数。
      </p>
      <textarea
        rows={6}
        value={credText}
        placeholder="email----password----clientId----refresh_token"
        onInput={(e) => setCredText(e.currentTarget.value)}
      />
      <label class="inline">
        mode{" "}
        <select value={credMode} onChange={(e) => setCredMode(e.currentTarget.value)}>
          <option value="append">append</option>
          <option value="replace">replace</option>
        </select>
      </label>
      <Button variant="ghost" busy={is("cred")} onClick={importCreds}>
        导入凭证
      </Button>
    </div>
  );
}
