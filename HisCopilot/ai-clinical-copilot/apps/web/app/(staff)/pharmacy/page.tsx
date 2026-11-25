"use client";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Empty, ErrorBox, Skeleton, StatusChip } from "@/components/ui";
import { fmtTime } from "@/lib/api";
import { IssueBadge } from "@/components/workspace/RxItems";

const COLS: [string, string, string][] = [["signed", "待接单", "医师已签署，等待药房接单"], ["pharmacy_review", "审核中", "已接单，正在审核"], ["approved", "待发药", "审核通过"], ["returned", "已退回", "退回医师修改中"], ["dispensed", "最近已发药", "最近 20 张"]];
const TYPE: Record<string, string> = { western: "西药", patent: "中成药", herbal: "中药饮片" };

export default function Pharmacy() {
  const router = useRouter();
  const { data, error, mutate } = useSWR("/pharmacy/queue", { refreshInterval: 20000 });
  return (
    <div className="space-y-4">
      <div><h1 className="h-page">审方发药</h1><p className="mt-1 text-sm text-ink-500">只显示医师<b>已签署</b>的处方；草稿对药房不可见。每 20 秒自动刷新。</p></div>
      {error ? <ErrorBox error={error} onRetry={() => mutate()} /> : !data ? <div className="grid gap-4 lg:grid-cols-3"><Skeleton className="h-48" /><Skeleton className="h-48" /><Skeleton className="h-48" /></div> : (
        <div className="grid gap-4 xl:grid-cols-3 lg:grid-cols-2">{COLS.map(([k, t, h]) => { const list: any[] = data[k] || []; return (
          <section key={k} className="card overflow-hidden"><div className="flex items-center gap-2 border-b border-ink-100 px-4 py-3"><h2 className="h-sec">{t}</h2><span className="tag-neutral">{list.length}</span><span className="ml-auto text-[11px] text-ink-400">{h}</span></div>
            {list.length === 0 ? <Empty title="暂无" /> : <ul className="divide-y divide-ink-50">{list.map((r) => (
              <li key={r.id}><button className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-brand-50/40" onClick={() => router.push(`/prescriptions/${r.id}`)}>
                <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="font-medium text-ink-900">{r.patient.name}</span><span className="tag-neutral">{TYPE[r.rx_type]}</span>{r.patient.allergies.length > 0 && <span className="tag-err" title={r.patient.allergies.join("、")}>过敏 {r.patient.allergies.length}</span>}</div>
                  <div className="mt-0.5 truncate text-xs text-ink-500">{r.rx_no} · {r.item_count} 项 · {r.doctor?.name} · {fmtTime(r.signed_at)}</div></div>
                {(r.safety_summary?.critical > 0 || r.safety_summary?.warning > 0) && <IssueBadge summary={r.safety_summary} />}<StatusChip s={r.status} /></button></li>))}</ul>}
          </section>); })}</div>)}
    </div>
  );
}
