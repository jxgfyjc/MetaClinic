"use client";
import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, ArrowRight, CalendarClock, ClipboardCheck, FileClock, MessageSquareWarning, Pill, Stethoscope, UserPlus } from "lucide-react";
import { useAuth, ROLE_HOME } from "@/lib/auth";
import { Empty, ErrorBox, SkeletonList, StatusChip } from "@/components/ui";
import { ago } from "@/lib/api";
import { EncRow, type Enc } from "@/components/EncRow";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

function Stat({ label, value, tone, href, icon: I }: { label: string; value: number | string; tone: string; href: string; icon: any }) {
  return (
    <Link href={href} className="card group flex items-center gap-3.5 p-4 transition hover:border-brand-200 hover:shadow-pop">
      <div className={`grid h-11 w-11 place-items-center rounded-xl ${tone}`}><I size={20} /></div>
      <div><div className="text-[26px] font-semibold leading-none tracking-tight text-ink-950">{value}</div><div className="mt-1 text-xs text-ink-500">{label}</div></div>
      <ArrowRight size={15} className="ml-auto text-ink-200 transition group-hover:translate-x-0.5 group-hover:text-brand-500" />
    </Link>
  );
}

export default function Workbench() {
  const { user, can } = useAuth();
  const router = useRouter();
  useEffect(() => { if (user && user.role === "PHARMACIST") router.replace("/pharmacy"); if (user && user.role === "ADMIN") router.replace("/admin"); }, [user, router]);
  const enc = useSWR<{ items: Enc[]; total: number }>("/encounters?scope=open&limit=100", { refreshInterval: 15000 });
  const fu = useSWR(can("followup.read") ? "/followup/inbox" : null, { refreshInterval: 60000 });
  const rx = useSWR(can("rx.read") && user?.role === "DOCTOR" ? "/prescriptions?status=returned&limit=20" : null);
  const items = enc.data?.items || [];
  const waiting = items.filter((e) => e.status === "WAITING");
  const inprog = items.filter((e) => e.status === "IN_PROGRESS");
  const needReview = items.filter((e) => e.pending_diagnoses > 0);
  const hour = new Date().getHours();
  const greet = hour < 6 ? "夜深了" : hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";

  if (user && (user.role === "PHARMACIST" || user.role === "ADMIN")) return null;
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="h-page">{greet}，{user?.display_name}</h1><p className="mt-1 text-sm text-ink-500">{new Date().toLocaleDateString("zh-CN", { month: "long", day: "numeric", weekday: "long" })} · 以下是需要您处理的事项</p></div>
        <Link href="/encounters/new" className="btn-primary"><UserPlus size={16} />新建接诊</Link>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="待接诊" value={enc.isLoading ? "…" : waiting.length} tone="bg-amber-50 text-amber-600" href="/encounters?tab=WAITING" icon={Stethoscope} />
        <Stat label="待审核的 AI 诊断" value={enc.isLoading ? "…" : needReview.length} tone="bg-brand-50 text-brand-600" href="/encounters?tab=review" icon={ClipboardCheck} />
        <Stat label="随访预警 / 患者消息" value={fu.data ? (fu.data.counts.alerts + fu.data.counts.messages) : "…"} tone="bg-danger-50 text-danger-600" href="/followups" icon={MessageSquareWarning} />
        <Stat label="药房退回处方" value={rx.data ? rx.data.items.length : "…"} tone="bg-jade-50 text-jade-600" href="/prescriptions?status=returned" icon={Pill} />
      </div>

      {fu.data?.alerts?.length > 0 && (
        <section className="card overflow-hidden border-danger-100">
          <div className="flex items-center gap-2 border-b border-danger-100 bg-danger-50/70 px-4 py-2.5"><AlertTriangle size={16} className="text-danger-600" /><h2 className="text-sm font-semibold text-danger-700">随访风险提示（{fu.data.alerts.length}）</h2><Link href="/followups" className="ml-auto text-xs text-danger-600 hover:underline">全部处理 →</Link></div>
          <ul className="divide-y divide-ink-50">{fu.data.alerts.slice(0, 4).map((a: any) => (
            <li key={a.id}><Link href="/followups" className="flex items-center gap-3 px-4 py-3 hover:bg-ink-50/60"><StatusChip s={a.level} /><span className="font-medium">{a.patient_name}</span><span className="truncate text-sm text-ink-500">{(a.rules || []).map((r: any) => r.detail).join("；")}</span><span className="ml-auto shrink-0 text-xs text-ink-400">{ago(a.created_at)}</span></Link></li>))}</ul>
        </section>)}

      <div className="grid gap-5 lg:grid-cols-[1.5fr_1fr]">
        <section className="card overflow-hidden">
          <div className="flex items-center justify-between border-b border-ink-100 px-4 py-3"><h2 className="h-sec">进行中的接诊</h2><Link href="/encounters" className="text-xs text-brand-600 hover:underline">查看全部</Link></div>
          {enc.error ? <div className="p-4"><ErrorBox error={enc.error} onRetry={() => enc.mutate()} /></div> : enc.isLoading ? <SkeletonList rows={3} /> : items.length === 0 ?
            <Empty title="当前没有待处理的接诊" hint="患者扫码提交资料、或预约到诊后会出现在这里" action={<Link href="/encounters/new" className="btn-secondary btn-sm mt-1">新建接诊</Link>} /> :
            <ul className="divide-y divide-ink-50">{items.slice(0, 8).map((e) => <EncRow key={e.id} e={e} />)}</ul>}
        </section>
        <div className="space-y-5">
          <section className="card overflow-hidden">
            <div className="border-b border-ink-100 px-4 py-3"><h2 className="h-sec">待处理清单</h2></div>
            <ul className="divide-y divide-ink-50 text-sm">
              <Todo icon={FileClock} label="AI 诊断候选待审核" n={needReview.length} href="/encounters?tab=review" />
              <Todo icon={Pill} label="未签署的处方草稿" n={items.reduce((a, e) => a + e.open_prescriptions, 0)} href="/encounters" />
              <Todo icon={CalendarClock} label="今日到期随访" n={fu.data?.counts?.due || 0} href="/followups" />
              <Todo icon={AlertTriangle} label="资料处理失败（语音/OCR）" n={items.reduce((a, e) => a + e.failed_artifacts, 0)} href="/encounters" tone="danger" />
            </ul>
          </section>
          <section className="card card-pad bg-gradient-to-br from-brand-50 to-white">
            <h2 className="h-sec">诊疗闭环</h2>
            <ol className="mt-2.5 space-y-1.5 text-[13px] text-ink-600">{["接诊 · 病史 / 语音 / 检验 / 舌象", "AI 预分析（后台自动，可随时复核）", "审核诊断 → 统一治疗方案", "处方（可多张）→ 安全校验 → 签署", "病历定稿 · 打印 / PDF", "随访 · 复诊"].map((t, i) => <li key={t} className="flex gap-2"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-brand-100 text-[11px] font-semibold text-brand-700">{i + 1}</span>{t}</li>)}</ol>
          </section>
        </div>
      </div>
    </div>
  );
}

function Todo({ icon: I, label, n, href, tone }: { icon: any; label: string; n: number; href: string; tone?: string }) {
  return <li><Link href={href} className="flex items-center gap-3 px-4 py-2.5 hover:bg-ink-50/60"><I size={16} className={n && tone === "danger" ? "text-danger-500" : "text-ink-400"} /><span className="flex-1 text-ink-700">{label}</span><span className={`min-w-[1.5rem] rounded-full px-2 text-center text-xs font-semibold ${n ? (tone === "danger" ? "bg-danger-50 text-danger-600" : "bg-brand-50 text-brand-700") : "bg-ink-50 text-ink-300"}`}>{n}</span></Link></li>;
}
