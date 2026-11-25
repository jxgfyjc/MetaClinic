"use client";
import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { ChevronRight, Plus, Search, UserPlus } from "lucide-react";
import { Empty, ErrorBox, SkeletonList, useDebounced, useToast } from "@/components/ui";
import { NewPatient } from "@/components/NewPatient";
import { api, errMsg, fmtDay } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function List() {
  const sp = useSearchParams(); const router = useRouter(); const toast = useToast(); const { can } = useAuth();
  const [q, setQ] = useState(sp.get("q") || ""); const dq = useDebounced(q, 300);
  const { data, error, isLoading, mutate } = useSWR(`/patients?limit=60${dq ? `&q=${encodeURIComponent(dq)}` : ""}`);
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="h-page">患者档案</h1><p className="mt-1 text-sm text-ink-500">以“人”为单位，每人一份长期档案。点击姓名查看历史就诊与病历。</p></div>
        {can("patient.write") && <button className="btn-primary" onClick={() => setOpen(true)}><UserPlus size={16} />新建患者</button>}</div>
      <div className="card overflow-hidden">
        <div className="border-b border-ink-100 p-3"><div className="relative max-w-md"><Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" /><input className="input pl-9" placeholder="姓名 / 手机 / 门诊号" value={q} onChange={(e) => setQ(e.target.value)} /></div></div>
        {error ? <div className="p-4"><ErrorBox error={error} onRetry={() => mutate()} /></div> : isLoading ? <SkeletonList rows={5} /> : !data?.items.length ?
          <Empty title={dq ? "没有找到匹配的患者" : "还没有患者档案"} hint={dq ? "请核对姓名或手机号，或新建患者" : "新建患者，或让患者扫码自助建档"} /> :
          <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>姓名</th><th>门诊号</th><th>性别 / 年龄</th><th>手机</th><th>过敏史</th><th>就诊次数</th><th>最近就诊</th><th /></tr></thead>
            <tbody>{data.items.map((p: any) => (
              <tr key={p.id} className="cursor-pointer" onClick={() => router.push(`/patients/${p.id}`)}>
                <td><div className="flex items-center gap-2.5"><span className="grid h-8 w-8 place-items-center rounded-full bg-brand-50 text-xs font-semibold text-brand-700">{p.name.slice(0, 1)}</span><span className="font-medium">{p.name}</span>{p.is_sample && <span className="tag-sim">示例</span>}</div></td>
                <td className="font-mono text-xs text-ink-500">{p.mrn}</td>
                <td>{p.gender || <span className="text-ink-300">未填写</span>} / {p.age != null ? `${p.age}岁` : <span className="text-ink-300">未填写</span>}</td>
                <td>{p.phone || <span className="text-ink-300">未填写</span>}</td>
                <td>{p.allergies.length ? <div className="flex flex-wrap gap-1">{p.allergies.slice(0, 3).map((a: any) => <span key={a.id} className="tag-err">{a.allergen}</span>)}{p.allergies.length > 3 && <span className="tag-neutral">+{p.allergies.length - 3}</span>}</div> : <span className="text-xs text-ink-300">未登记</span>}</td>
                <td>{p.encounter_count}</td><td className="text-ink-500">{fmtDay(p.last_visit_at)}</td><td><ChevronRight size={15} className="text-ink-300" /></td>
              </tr>))}</tbody></table></div>}
      </div>
      <NewPatient open={open} onClose={() => setOpen(false)} onDone={(id) => { toast("ok", "患者档案已创建"); mutate(); router.push(`/patients/${id}`); }} />
    </div>
  );
}

export default function PatientsPage() { return <Suspense><List /></Suspense>; }
