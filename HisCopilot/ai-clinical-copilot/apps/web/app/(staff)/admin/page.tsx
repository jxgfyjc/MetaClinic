"use client";
import useSWR from "swr";
import Link from "next/link";
import { Activity, AlertTriangle, CheckCircle2, Cpu, ShieldAlert, Users, XCircle } from "lucide-react";
import { Banner, ErrorBox, Skeleton, StatusChip } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { fmtTime } from "@/lib/api";

const MODEL: Record<string, string> = { llm_main: "主模型（Qwen 27B）", llm_tcm: "中医模型（ShizhenGPT）", asr: "语音识别", ocr: "OCR" };
const ENC: Record<string, string> = { WAITING: "待接诊", IN_PROGRESS: "接诊中", PENDING_CONFIRM: "待确认", COMPLETED: "已完成", CANCELLED: "已取消" };
const JOB: Record<string, string> = { asr: "语音识别", ocr: "OCR", tongue: "舌象", analysis: "AI 预分析", plan: "方案起草" };

export default function Admin() {
  const { can } = useAuth();
  const st = useSWR("/admin/stats", { refreshInterval: 20000 }); const sys = useSWR("/system/status", { refreshInterval: 20000 }); const jobs = useSWR(can("job.manage") ? "/admin/jobs?limit=30" : null, { refreshInterval: 20000 });
  const ver = useSWR(can("audit.read") ? "/admin/audit/verify" : null, { revalidateOnFocus: false });
  return (
    <div className="space-y-5">
      <div><h1 className="h-page">运行状态</h1><p className="mt-1 text-sm text-ink-500">模型服务、任务队列、审计完整性一览。管理员不能签署处方或定稿病历（职责分离）。</p></div>
      {sys.data && <Banner kind="warn" title="HIS：模拟模式">{sys.data.his.notice}。处方“提交 HIS”仅生成以 MOCK- 开头的演练回执。</Banner>}
      <div className="grid gap-4 lg:grid-cols-2">
        <section className="card card-pad"><h2 className="h-sec mb-3 flex items-center gap-2"><Cpu size={16} className="text-brand-600" />模型服务</h2>
          {sys.error ? <ErrorBox error={sys.error} onRetry={() => sys.mutate()} /> : !sys.data ? <Skeleton className="h-28 w-full" /> : (
            <ul className="space-y-2">{Object.entries<any>(sys.data.models).map(([k, v]) => (
              <li key={k} className="flex items-center gap-2 text-sm">{v.ok ? <CheckCircle2 size={16} className="text-jade-600" /> : <XCircle size={16} className="text-danger-600" />}<span className="font-medium">{MODEL[k] || k}</span>
                <span className={`ml-auto truncate text-xs ${v.ok ? "text-ink-400" : "text-danger-600"}`}>{v.fake ? "测试替身" : v.ok ? (v.latency_ms != null ? `响应 ${v.latency_ms} ms` : "正常") : `不可用${v.error ? `（${v.error}）` : ""}`}</span></li>))}</ul>)}
          {sys.data && <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 border-t border-ink-50 pt-3 text-xs text-ink-500"><span>AI 模式：<b className={sys.data.ai_mode === "live" ? "text-jade-700" : "text-amber-700"}>{sys.data.ai_mode === "live" ? "真实模型" : "测试替身（fake）"}</b></span><span>版本 {sys.data.version}</span>
            <span>队列：{Object.entries<any>(sys.data.queue_depth).map(([k, n]) => `${k}:${n}`).join("  ") || "空闲"}</span></div>}</section>
        <section className="card card-pad"><h2 className="h-sec mb-3 flex items-center gap-2"><Activity size={16} className="text-brand-600" />概况</h2>
          {st.error ? <ErrorBox error={st.error} onRetry={() => st.mutate()} /> : !st.data ? <Skeleton className="h-28 w-full" /> : (<>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><Stat label="患者" v={st.data.patients} /><Stat label="今日就诊" v={st.data.encounters_today} /><Stat label="未处理预警" v={st.data.open_alerts} tone={st.data.open_alerts ? "err" : ""} /><Stat label="24h 失败任务" v={st.data.failed_jobs_24h} tone={st.data.failed_jobs_24h ? "err" : ""} /></div>
            <div className="mt-3 flex flex-wrap gap-1.5 text-xs">{Object.entries<number>(st.data.encounters_by_status).map(([k, n]) => <span key={k} className="tag-neutral">{ENC[k] || k} {n}</span>)}</div></>)}
          {ver.data && <div className={`mt-3 flex items-center gap-2 rounded-lg px-3 py-2 text-xs ${ver.data.ok ? "bg-jade-50 text-jade-700" : "bg-danger-50 text-danger-700"}`}><ShieldAlert size={14} />{ver.data.ok ? `审计日志哈希链校验通过（已校验 ${ver.data.checked} 条）` : `审计日志校验失败（第 ${ver.data.first_bad_id} 条）：${ver.data.reason}`}</div>}</section>
      </div>
      {can("job.manage") && (
        <section className="card overflow-hidden"><div className="border-b border-ink-100 px-4 py-3"><h2 className="h-sec">最近的后台任务</h2></div>
          {jobs.error ? <div className="p-4"><ErrorBox error={jobs.error} onRetry={() => jobs.mutate()} /></div> : !jobs.data ? <Skeleton className="m-4 h-32" /> : jobs.data.items.length === 0 ? <div className="p-6 text-center text-sm text-ink-400">暂无任务</div> : (
            <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>类型</th><th>状态</th><th>尝试</th><th>错误</th><th>创建</th><th>结束</th></tr></thead>
              <tbody>{jobs.data.items.map((j: any) => <tr key={j.id}><td>{JOB[j.kind] || j.kind}</td><td><StatusChip s={j.status} /></td><td>{j.attempts}</td><td className="max-w-[22rem] truncate text-xs text-danger-600" title={j.error_detail || ""}>{j.error_code ? `[${j.error_code}] ` : ""}{j.error_detail || ""}</td><td className="text-ink-500">{fmtTime(j.created_at)}</td><td className="text-ink-500">{fmtTime(j.finished_at)}</td></tr>)}</tbody></table></div>)}</section>)}
      <div className="flex gap-3 text-sm">{can("user.manage") && <Link className="btn-secondary" href="/admin/users"><Users size={14} />账号权限</Link>}{can("audit.read") && <Link className="btn-secondary" href="/admin/audit">审计日志</Link>}</div>
    </div>
  );
}
function Stat({ label, v, tone }: { label: string; v: number; tone?: string }) { return <div className="rounded-xl border border-ink-100 p-3"><div className={`text-2xl font-semibold ${tone === "err" ? "text-danger-600" : "text-ink-950"}`}>{v}</div><div className="text-xs text-ink-500">{label}</div></div>; }
