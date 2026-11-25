"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { Search } from "lucide-react";
import { Empty, ErrorBox, SkeletonList, StatusChip, Tabs, useDebounced } from "@/components/ui";
import { fmtTime } from "@/lib/api";
import { IssueBadge } from "@/components/workspace/RxItems";

const TYPE: Record<string, string> = { western: "西药", patent: "中成药", herbal: "中药饮片" };
const TABS = [["all", "全部"], ["draft", "草稿"], ["returned", "药房退回"], ["signed", "已签署"], ["pharmacy_review", "药房审核中"], ["approved", "审核通过"], ["dispensed", "已发药"], ["voided", "已作废"]] as const;

function List() {
  const router = useRouter(); const sp = useSearchParams(); const tab = sp.get("status") || "all"; const [q, setQ] = useState(""); const dq = useDebounced(q, 300);
  const { data, error, isLoading, mutate } = useSWR(`/prescriptions?limit=100${tab !== "all" ? `&status=${tab}` : ""}${dq ? `&q=${encodeURIComponent(dq)}` : ""}`);
  return (
    <div className="space-y-4">
      <div><h1 className="h-page">处方</h1><p className="mt-1 text-sm text-ink-500">处方是独立保存的单据，与治疗方案分开；一次就诊可有多张。</p></div>
      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 px-2 pt-1"><div className="min-w-0 flex-1"><Tabs value={tab} onChange={(v) => router.replace(v === "all" ? "/prescriptions" : `/prescriptions?status=${v}`)} items={TABS.map(([k, l]) => ({ key: k, label: l }))} /></div>
          <div className="relative mr-2 w-full sm:w-64"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" /><input className="input rounded-full py-1.5 pl-8 text-xs" placeholder="姓名 / 处方号 / 门诊号" value={q} onChange={(e) => setQ(e.target.value)} /></div></div>
        {error ? <div className="p-4"><ErrorBox error={error} onRetry={() => mutate()} /></div> : isLoading ? <SkeletonList rows={5} /> : !data?.items.length ? <Empty title="没有符合条件的处方" /> : (
          <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>处方号</th><th>患者</th><th>类型</th><th>名称</th><th>药味/药品</th><th>状态</th><th>安全提示</th><th>开具</th></tr></thead>
            <tbody>{data.items.map((r: any) => (
              <tr key={r.id} className="cursor-pointer" onClick={() => router.push(`/prescriptions/${r.id}`)}><td className="font-mono text-xs text-ink-500">{r.rx_no}</td><td className="font-medium">{r.patient.name}</td><td>{TYPE[r.rx_type]}</td><td>{r.title}</td><td>{r.item_count}</td>
                <td><StatusChip s={r.status} /></td><td>{["draft", "returned"].includes(r.status) ? <IssueBadge summary={r.safety_summary} /> : <span className="text-ink-300">—</span>}</td><td className="text-ink-500">{r.doctor?.name} · {fmtTime(r.created_at)}</td></tr>))}</tbody></table></div>)}
      </div>
    </div>
  );
}
export default function Page() { return <Suspense><List /></Suspense>; }
