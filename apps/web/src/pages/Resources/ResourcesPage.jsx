// Resources page: 节点·Clash | 节点·catalog | 邮箱 | 导入
// Batch-2 IA: Nodes 子 tab 提升到顶层（去掉三层 tab 嵌套）。
// Clash/catalog 仍常驻挂载（active 语义），筛选/表单输入切 tab 不丢。
import { useEffect, useState } from "preact/hooks";
import { Tabs, PageHeader } from "../../ui/index.js";
import { ClashPanel } from "./nodes/ClashPanel.jsx";
import { CatalogPanel } from "./nodes/CatalogPanel.jsx";
import { MailTab } from "./MailTab.jsx";
import { ImportTab } from "./ImportTab.jsx";
import "../../styles/resources.css";
import "../../styles/accounts.css"; // table.data / badge / filter-bar shared

const TABS = [
  { id: "clash", label: "节点 · Clash" },
  { id: "catalog", label: "节点 · catalog" },
  { id: "mail", label: "邮箱" },
  { id: "import", label: "导入" },
];

function tabFromHash() {
  try {
    const h = location.hash || "";
    const m = h.match(/[?&]tab=([a-z]+)/i);
    const t = m && m[1];
    if (t === "nodes") return "clash"; // legacy two-level URL
    if (t && TABS.some((x) => x.id === t)) return t;
  } catch {
    /* ignore */
  }
  return "clash";
}

export function ResourcesPage() {
  const [tab, setTab] = useState(tabFromHash);

  useEffect(() => {
    const base = "#/resources";
    const next = tab === "clash" ? base : `${base}?tab=${tab}`;
    if (location.hash !== next && (location.hash.startsWith("#/resources") || !location.hash)) {
      location.hash = next;
    }
  }, [tab]);

  return (
    <section class="page page-resources">
      <PageHeader
        title="资源"
        hint={
          <>
            节点池（Clash + catalog）、邮箱接码与导入。Hotmail 凭证仅在此「邮箱」tab。
          </>
        }
      />

      <div class="card">
        <Tabs items={TABS} value={tab} onChange={setTab} />
      </div>

      {/* Clash/catalog stay mounted（原 Nodes 子 tab 语义）；mail/import 按需挂载。 */}
      <div class="resources-tab nodes-tab" hidden={tab !== "clash" && tab !== "catalog"}>
        <ClashPanel active={tab === "clash"} />
        <CatalogPanel active={tab === "catalog"} />
      </div>
      {tab === "mail" ? <MailTab /> : null}
      {tab === "import" ? <ImportTab /> : null}
    </section>
  );
}
