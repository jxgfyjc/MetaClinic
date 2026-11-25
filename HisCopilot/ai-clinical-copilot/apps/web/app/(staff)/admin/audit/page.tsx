"use client";
import { Fragment, useState } from "react";
import useSWR from "swr";
import { Search, ShieldCheck } from "lucide-react";
import { api, fmtTime } from "@/lib/api";
import { Banner, Empty, ErrorBox, SkeletonList, Spinner, useDebounced } from "@/components/ui";
import { useAction } from "@/lib/useAction";

const OUT: Record<string, string> = { ok: "tag-ok", denied: "tag-err", error: "tag-err" };

export default function Audit() {
  const [q, setQ] = useState(""); const [action, setAction] = useState(""); const [outcome, setOutcome] = useState(""); const [page, setPage] = useState(0); const dq = useDebounced(q, 300);
  const LIM = 50;
  const { data, error, isLoading, mutate } = useSWR(`/admin/audit?limit=${LIM}&offset=${page * LIM}${dq ? `&q=${encodeURIComponent(dq)}` : ""}${action ? `&action=${encodeURIComponent(action)}` : ""}${outcome ? `&outcome=${outcome}` : ""}`);
  const act = useAction(); const [ver, setVer] = useState<any>(null);
  const [open, setOpen] = useState<number | null>(null);
  const pages = data ? Math.max(1, Math.ceil(data.total / LIM)) : 1;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="h-page">审计日志</h1><p className="mt-1 text-sm text-ink-500">关键操作（登录、诊断、方案、处方签署、病历定稿、越权尝试）不可删改，采用哈希链防篡改。</p></div>
        <button className="btn-secondary" disabled={act.any} onClick={() => act.run("v", async () => setVer(await api.get("/admin/audit/verify")))}>{act.is("v") ? <Spinner size={14} /> : <ShieldCheck size={14} />}校验日志完整性</button></div>
      {ver && <Banner kind={ver.ok ? "ok" : "err"} title={ver.ok ? `校验通过：已验证 ${ver.checked} 条记录的哈希链` : `校验失败（第 ${ver.first_bad_id} 条）`}>{ver.ok ? "未发现被删除或修改的记录。" : ver.reason}</Banner>}
      <div className="card overflow-hidden">
        <div className="flex flex-wrap items-center gap-2 border-b border-ink-100 p-3">
          <div className="relative w-full sm:w-64"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" /><input className="input py-1.5 pl-8 text-xs" placeholder="搜索摘要" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} aria-label="搜索摘要" /></div>
          <select className="input w-auto py-1.5 text-xs" value={action} onChange={(e) => { setAction(e.target.value); setPage(0); }} aria-label="操作类型"><option value="">全部操作</option>{["auth", "access.denied", "patient", "encounter", "analysis", "dx", "plan", "rx", "record", "followup", "intake", "user", "catalog"].map((a) => <option key={a} value={a}>{a}</option>)}</select>
          <select className="input w-auto py-1.5 text-xs" value={outcome} onChange={(e) => { setOutcome(e.target.value); setPage(0); }} aria-label="结果"><option value="">全部结果</option><option value="ok">成功</option><option value="denied">被拒绝</option></select>
          <span className="ml-auto text-xs text-ink-400">共 {data?.total ?? "…"} 条</span></div>
        {error ? <div className="p-4"><ErrorBox error={error} onRetry={() => mutate()} /></div> : isLoading ? <SkeletonList rows={6} /> : !data?.items.length ? <Empty title="没有符合条件的记录" /> : (
          <div className="overflow-x-auto"><table className="tbl text-[13px]"><thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>摘要</th><th>结果</th><th>IP</th></tr></thead>
            <tbody>{data.items.map((e: any) => (<Fragment key={e.id}>
              <tr className="cursor-pointer" onClick={() => setOpen(open === e.id ? null : e.id)}><td className="whitespace-nowrap text-ink-500">{fmtTime(e.ts)}</td><td className="whitespace-nowrap">{e.actor}<span className="ml-1 text-[11px] text-ink-400">{e.actor_kind === "patient" ? "患者" : e.actor_kind === "system" ? "系统" : e.role}</span></td><td className="font-mono text-xs">{e.action}</td><td className="max-w-[28rem] truncate">{e.summary}</td><td><span className={OUT[e.outcome] || "tag-neutral"}>{e.outcome === "ok" ? "成功" : e.outcome === "denied" ? "被拒绝" : e.outcome}</span></td><td className="font-mono text-xs text-ink-400">{e.ip}</td></tr>
              {open === e.id && <tr><td colSpan={6} className="bg-ink-50/70"><div className="space-y-1 p-2 text-xs text-ink-600"><div>{e.summary}</div>{e.entity && <div>对象：<span className="font-mono">{e.entity}</span></div>}<div>请求号：<span className="font-mono">{e.request_id}</span></div>{e.detail && Object.keys(e.detail).length > 0 && <pre className="overflow-auto rounded bg-white p-2 font-mono text-[11px]">{JSON.stringify(e.detail, null, 2)}</pre>}</div></td></tr>}</Fragment>))}</tbody></table></div>)}
        <div className="flex items-center justify-end gap-2 border-t border-ink-100 p-2 text-xs"><button className="btn-secondary btn-sm" disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button><span className="text-ink-500">{page + 1} / {pages}</span><button className="btn-secondary btn-sm" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>下一页</button></div>
      </div>
    </div>
  );
}
