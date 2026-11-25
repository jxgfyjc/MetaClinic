"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { CalendarPlus, CheckCircle2, Copy, HeartPulse, Link2, MessageSquareHeart, Plus, Send, Trash2, XCircle } from "lucide-react";
import { api, errMsg, fmtDay, fmtTime } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Banner, Empty, ErrorBox, Skeleton, Spinner, StatusChip, useConfirm, useToast } from "@/components/ui";
import { QrBox } from "@/components/QrBox";
import { useAction } from "@/lib/useAction";
import { Card, LinesEditor } from "./bits";
import { useWs } from "./ctx";

const KIND: Record<string, string> = { symptom_check: "症状随访", revisit_reminder: "复诊提醒", medication_check: "用药依从性" };
const DUE: Record<string, [string, string]> = { upcoming: ["未到期", "tag-neutral"], due: ["今日到期", "tag-warn"], overdue: ["已逾期", "tag-err"], answered: ["患者已答", "tag-brand"], closed: ["已关闭", "tag-neutral"], cancelled: ["已取消", "tag-neutral"] };

export function FollowupPanel() {
  const ws = useWs(); const { b, eid } = ws; const { can } = useAuth(); const toast = useToast(); const act = useAction(); const confirm = useConfirm();
  const writable = can("followup.write") && ws.mayAct;                              // follow-up is planned AFTER the visit is finalized
  const q = useSWR(can("followup.read") ? `/encounters/${eid}/followup-plan` : null, { revalidateOnFocus: false });
  const plan = q.data?.plan;
  const done = b.encounter.status === "COMPLETED";
  const [sched, setSched] = useState<any[] | null>(null); const [advice, setAdvice] = useState("");
  useEffect(() => { if (plan?.status === "proposed") { setSched(plan.schedule); setAdvice(plan.revisit_advice || ""); } else setSched(null); }, [plan?.id, plan?.status]);   // eslint-disable-line

  const propose = () => act.run("propose", async () => { await api.post(`/encounters/${eid}/followup-plan`, { revisit_advice: b.plan?.content?.followup_advice || "" }); await q.mutate(); }, { ok: "已生成随访计划草案，请确认后启用" });
  const activate = async () => {
    if (!sched?.length) return toast("warn", "至少保留一个随访节点");
    const c = await confirm({ title: "启用随访计划？", body: "启用后将按设定天数生成随访任务；任务到期时患者可通过您发送的链接回答问题。系统不会自动给患者发消息。", confirmText: "启用" });
    if (c.ok) act.run("activate", async () => { await api.post(`/followup-plans/${plan.id}/activate`, { schedule: sched }); await q.mutate(); }, { ok: "随访计划已启用" });
  };
  const cancel = async () => { const c = await confirm({ title: "取消随访计划？", body: "未到期的随访任务将被取消。已回答的记录保留。", confirmText: "取消计划", danger: true }); if (c.ok) act.run("cancel", async () => { await api.post(`/followup-plans/${plan.id}/cancel`); await q.mutate(); }, { ok: "已取消" }); };

  if (!can("followup.read")) return <Card><Empty title="您没有查看随访的权限" /></Card>;
  return (
    <div className="space-y-4">
      {!done && <Banner kind="info">随访通常在病历定稿后启动。您也可以现在先规划。</Banner>}

      <Card title="随访计划" icon={<HeartPulse size={16} className="text-brand-600" />}>
        {q.error ? <ErrorBox error={q.error} onRetry={() => q.mutate()} /> : q.isLoading ? <Skeleton className="h-24 w-full" /> : !plan ? (
          <Empty title="还没有随访计划" hint="生成一份草案，您可以调整随访节点与问题后再启用。草案不会触达患者。" action={writable ? <button className="btn-primary mt-1" disabled={act.any} onClick={propose}>{act.is("propose") ? <Spinner size={14} /> : <Plus size={14} />}生成随访计划草案</button> : undefined} />
        ) : plan.status === "proposed" && sched ? (
          <div className="space-y-3">
            <Banner kind="warn" title="草案：尚未启用，不会通知患者" />
            {sched.map((n, i) => (
              <div key={i} className="rounded-xl border border-ink-100 p-3">
                <div className="flex flex-wrap items-center gap-2"><span className="text-sm text-ink-600">就诊后第</span><input className="input w-20 py-1" type="number" min={0} max={365} aria-label="天数" value={n.day} disabled={!writable} onChange={(e) => setSched(sched.map((x, j) => (j === i ? { ...x, day: Number(e.target.value) } : x)))} /><span className="text-sm text-ink-600">天</span>
                  <select className="input w-auto py-1" aria-label="类型" disabled={!writable} value={n.kind} onChange={(e) => setSched(sched.map((x, j) => (j === i ? { ...x, kind: e.target.value } : x)))}>{Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
                  {writable && <button type="button" className="btn-ghost btn-sm ml-auto text-ink-400 hover:text-danger-600" aria-label="删除节点" onClick={() => setSched(sched.filter((_, j) => j !== i))}><Trash2 size={14} /></button>}</div>
                <div className="mt-2"><div className="mb-1 text-xs text-ink-400">向患者询问的问题（每行一个）</div><LinesEditor rows={3} disabled={!writable} value={n.questions || []} onChange={(v) => setSched(sched.map((x, j) => (j === i ? { ...x, questions: v } : x)))} /></div>
              </div>))}
            {writable && <div className="flex flex-wrap gap-2"><button className="btn-secondary btn-sm" onClick={() => setSched([...sched, { day: 7, kind: "symptom_check", questions: ["症状较上次就诊是否好转？（明显好转 / 好转 / 无变化 / 加重）"] }])}><Plus size={13} />添加节点</button>
              <button className="btn-primary ml-auto" disabled={act.any || !sched.length} onClick={activate}>{act.is("activate") ? <Spinner size={14} /> : <CheckCircle2 size={14} />}确认并启用</button>
              <button className="btn-ghost" disabled={act.any} onClick={cancel}>放弃草案</button></div>}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm"><StatusChip s={plan.status} /><span className="text-ink-500">{plan.tasks.length} 个随访任务</span>{writable && plan.status === "active" && <button className="btn-ghost btn-sm ml-auto text-danger-600" disabled={act.any} onClick={cancel}><XCircle size={13} />取消计划</button>}</div>
            {plan.revisit_advice && <div className="rounded-lg bg-ink-50 p-2.5 text-sm text-ink-700"><span className="text-ink-400">复诊建议：</span>{plan.revisit_advice}</div>}
            <ul className="divide-y divide-ink-50 rounded-xl border border-ink-100">{plan.tasks.map((t: any) => <TaskRow key={t.id} t={t} writable={writable} onChanged={() => q.mutate()} />)}</ul>
          </div>)}
      </Card>

      {writable && <Link href={`/appointments?pid=${b.patient.id}&source=${eid}`} className="btn-secondary inline-flex"><CalendarPlus size={15} />为该患者预约复诊</Link>}
      <ReleaseCard writable={can("record.write") && ws.mayAct && !!done} done={done} />
    </div>
  );
}

function TaskRow({ t, writable, onChanged }: { t: any; writable: boolean; onChanged: () => void }) {
  const toast = useToast(); const act = useAction(); const [link, setLink] = useState("");
  const [d, dc] = DUE[t.due_state] || [t.due_state, "tag-neutral"];
  const mk = () => act.run(`link-${t.id}`, async () => { const r = await api.post(`/followup-tasks/${t.id}/portal-link`); setLink(r.url); });
  return (
    <li className="p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm"><span className="font-medium text-ink-800">第 {t.seq} 次</span><span className="tag-neutral">{KIND[t.kind] || t.kind}</span><span className={dc}>{d}</span><span className="text-xs text-ink-400">应于 {fmtDay(t.due_at)}</span>
        {writable && ["scheduled", "answered"].includes(t.status) && <span className="ml-auto flex gap-1.5">
          {t.status === "scheduled" && <button className="btn-secondary btn-sm" disabled={act.any} onClick={mk}>{act.is(`link-${t.id}`) ? <Spinner size={12} /> : <Link2 size={12} />}生成患者答题链接</button>}
          {t.status === "answered" && <button className="btn-secondary btn-sm" disabled={act.any} onClick={() => act.run(`close-${t.id}`, async () => { await api.post(`/followup-tasks/${t.id}/close`); onChanged(); }, { ok: "已关闭" })}>标记已处理</button>}</span>}</div>
      {link && (<div className="mt-2 flex flex-wrap items-center gap-3 rounded-lg bg-ink-50 p-2.5"><QrBox text={link} size={96} /><div className="min-w-0 flex-1 text-xs text-ink-500"><div>发给患者或让患者扫码（30 天有效，仅能访问该患者自己的随访）。链接仅此一次显示：</div>
        <div className="mt-1 break-all rounded bg-white p-1.5 font-mono text-[11px]">{link}</div><button className="btn-secondary btn-sm mt-1.5" onClick={() => { navigator.clipboard?.writeText(link).then(() => toast("ok", "已复制"), () => toast("warn", "复制失败，请手动选择")); }}><Copy size={12} />复制链接</button></div></div>)}
    </li>);
}

function ReleaseCard({ writable, done }: { writable: boolean; done: boolean }) {
  const ws = useWs(); const { b, eid } = ws; const act = useAction(); const confirm = useConfirm();
  const [summary, setSummary] = useState(""); const [advice, setAdvice] = useState<string[]>([]); const [revisit, setRevisit] = useState("");
  const fill = () => { const c = b.plan?.content || {}; setSummary((s) => s || c.summary || ""); setAdvice((a) => (a.length ? a : c.lifestyle || [])); setRevisit((r) => r || c.followup_advice || ""); };
  const send = async () => {
    const c = await confirm({ title: "发布给患者？", body: "患者通过随访链接可以看到这段摘要。请只写适合患者阅读的内容——诊断、处方细节、AI 分析不会被患者看到，除非您在这里写进去。", confirmText: "发布" });
    if (c.ok) act.run("rel", async () => { await api.post(`/encounters/${eid}/release-summary`, { summary: summary.trim(), advice, revisit }); }, { ok: "已发布患者医嘱摘要" });
  };
  return (
    <Card title="患者医嘱摘要" icon={<MessageSquareHeart size={16} className="text-brand-600" />} right={<span className="hint">患者唯一能看到的临床内容，由您撰写并发布</span>}>
      {!done ? <div className="text-sm text-ink-400">病历定稿后可发布。</div> : !writable ? <div className="text-sm text-ink-400">仅医师可发布。</div> : (
        <div className="space-y-3">
          <div className="flex"><button className="btn-ghost btn-sm" onClick={fill}>从治疗方案带入（可编辑）</button></div>
          <textarea className="input min-h-[84px]" value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="用患者能看懂的话写本次就诊的结论与注意事项" aria-label="摘要" />
          <div><div className="mb-1 text-xs text-ink-400">医嘱要点（每行一条）</div><LinesEditor rows={3} value={advice} onChange={setAdvice} /></div>
          <input className="input" value={revisit} onChange={(e) => setRevisit(e.target.value)} placeholder="复诊建议，如：2 周后复诊，症状加重随时就诊" aria-label="复诊建议" />
          <button className="btn-primary" disabled={act.any || summary.trim().length < 2} onClick={send}>{act.is("rel") ? <Spinner size={14} /> : <Send size={14} />}发布给患者</button></div>)}
    </Card>);
}
