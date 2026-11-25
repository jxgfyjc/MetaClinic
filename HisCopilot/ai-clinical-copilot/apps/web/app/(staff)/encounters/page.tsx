"use client";
import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { Plus, Search } from "lucide-react";
import { Empty, ErrorBox, SkeletonList, Tabs, useDebounced } from "@/components/ui";
import { EncRow } from "@/components/EncRow";

const TABS = [["all", "今日全部"], ["WAITING", "待接诊"], ["IN_PROGRESS", "接诊中"], ["review", "诊断待审"], ["PENDING_CONFIRM", "待确认"], ["COMPLETED", "已完成"]] as const;

function List() {
  const sp = useSearchParams(); const router = useRouter();
  const tab = (sp.get("tab") || "all") as string;
  const [q, setQ] = useState(sp.get("q") || ""); const dq = useDebounced(q, 300);
  const scope = tab === "COMPLETED" ? "completed" : tab === "all" ? "today" : "open";
  const { data, error, isLoading, mutate } = useSWR(`/encounters?scope=${scope}&limit=100${dq ? `&q=${encodeURIComponent(dq)}` : ""}`, { refreshInterval: 15000 });
  let items: any[] = data?.items || [];
  if (tab === "WAITING" || tab === "IN_PROGRESS" || tab === "PENDING_CONFIRM") items = items.filter((e) => e.status === tab);
  if (tab === "review") items = items.filter((e) => e.pending_diagnoses > 0);
  const count = (f: (e: any) => boolean) => (data?.items || []).filter(f).length;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="h-page">今日接诊</h1><p className="mt-1 text-sm text-ink-500">以“就诊”为单位：一位患者可有多次就诊。点击进入对应的诊疗室。</p></div>
        <Link href="/encounters/new" className="btn-primary"><Plus size={16} />新建接诊</Link></div>
      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-3 px-2 pt-1">
          <div className="min-w-0 flex-1"><Tabs value={tab as any} onChange={(v) => router.replace(v === "all" ? "/encounters" : `/encounters?tab=${v}`)}
            items={TABS.map(([k, l]) => ({ key: k as any, label: l, count: k === "WAITING" ? count((e) => e.status === "WAITING") : k === "review" ? count((e) => e.pending_diagnoses > 0) : undefined }))} /></div>
          <div className="relative mr-2 w-full sm:w-64"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" /><input className="input rounded-full py-1.5 pl-8 text-xs" placeholder="姓名 / 手机 / 就诊号" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        </div>
        {error ? <div className="p-4"><ErrorBox error={error} onRetry={() => mutate()} /></div> : isLoading ? <SkeletonList rows={5} /> : items.length === 0 ?
          <Empty title="没有符合条件的就诊" hint={dq ? "换个关键词试试" : "患者扫码提交或新建接诊后会出现在这里"} /> : <ul className="divide-y divide-ink-50">{items.map((e) => <EncRow key={e.id} e={e} />)}</ul>}
      </div>
    </div>
  );
}
export default function EncountersPage() { return <Suspense><List /></Suspense>; }
