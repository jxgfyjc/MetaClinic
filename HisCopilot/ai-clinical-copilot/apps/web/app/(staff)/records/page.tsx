"use client";
import { Suspense, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Search } from "lucide-react";
import { Empty, ErrorBox, SkeletonList, StatusChip, Tabs, useDebounced } from "@/components/ui";
import { fmtTime } from "@/lib/api";

function List() {
  const router = useRouter(); const [tab, setTab] = useState("all"); const [q, setQ] = useState(""); const dq = useDebounced(q, 300);
  const { data, error, isLoading, mutate } = useSWR(`/records?limit=100${tab !== "all" ? `&status=${tab}` : ""}${dq ? `&q=${encodeURIComponent(dq)}` : ""}`);
  return (
    <div className="space-y-4">
      <div><h1 className="h-page">病历</h1><p className="mt-1 text-sm text-ink-500">每次就诊一份病历，保留全部版本。定稿后只能“更正”，不可悄悄改写。</p></div>
      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 px-2 pt-1"><div className="min-w-0 flex-1"><Tabs value={tab} onChange={setTab} items={[{ key: "all", label: "全部" }, { key: "draft", label: "草稿" }, { key: "finalized", label: "已定稿" }]} /></div>
          <div className="relative mr-2 w-full sm:w-64"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" /><input className="input rounded-full py-1.5 pl-8 text-xs" placeholder="姓名 / 病历号 / 门诊号" value={q} onChange={(e) => setQ(e.target.value)} /></div></div>
        {error ? <div className="p-4"><ErrorBox error={error} onRetry={() => mutate()} /></div> : isLoading ? <SkeletonList rows={5} /> : !data?.items.length ? <Empty title="没有符合条件的病历" hint="病历在接诊时自动创建为草稿" /> : (
          <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>病历号</th><th>患者</th><th>主诉</th><th>诊断</th><th>状态</th><th>版本</th><th>更新</th></tr></thead>
            <tbody>{data.items.map((r: any) => (
              <tr key={r.id} className="cursor-pointer" onClick={() => router.push(`/records/${r.id}`)}><td className="font-mono text-xs text-ink-500">{r.record_no}</td><td className="font-medium">{r.patient.name}</td><td className="max-w-[16rem] truncate text-ink-600">{r.chief_complaint || "—"}</td>
                <td className="max-w-[16rem] truncate">{r.diagnoses.length ? r.diagnoses.join("、") : <span className="text-ink-300">—</span>}</td><td><StatusChip s={r.status} /></td><td>v{r.version_no}</td><td className="text-ink-500">{fmtTime(r.updated_at)}</td></tr>))}</tbody></table></div>)}
      </div>
    </div>
  );
}
export default function Page() { return <Suspense><List /></Suspense>; }
