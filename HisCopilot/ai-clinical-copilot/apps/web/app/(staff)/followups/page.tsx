"use client";
import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, Check, MessageSquareReply, Send, Stethoscope } from "lucide-react";
import { api, ago, errMsg, fmtDay, fmtTime, newKey } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Banner, Empty, ErrorBox, Modal, Skeleton, Spinner, StatusChip, Tabs, useToast } from "@/components/ui";
import { useAction } from "@/lib/useAction";

const KIND: Record<string, string> = { symptom_check: "症状随访", revisit_reminder: "复诊提醒", medication_check: "用药依从性" };

export default function Followups() {
  const { can } = useAuth(); const [tab, setTab] = useState("alerts");
  const { data, error, mutate } = useSWR("/followup/inbox", { refreshInterval: 30000 });
  const [task, setTask] = useState<any>(null); const [chat, setChat] = useState<any>(null); const [alert, setAlert] = useState<any>(null);
  if (error) return <ErrorBox error={error} onRetry={() => mutate()} />;
  if (!data) return <div className="space-y-3"><Skeleton className="h-10 w-64" /><Skeleton className="h-64 w-full" /></div>;
  const c = data.counts;
  return (
    <div className="space-y-4">
      <div><h1 className="h-page">随访与预警</h1><p className="mt-1 text-sm text-ink-500">患者回答由<b>规则优先</b>判定风险：命中高危关键词一定会生成预警，AI 不会把规则判定的风险降级。最终处置由医师决定。</p></div>
      <div className="card overflow-hidden">
        <Tabs value={tab} onChange={setTab} items={[{ key: "alerts", label: "风险预警", count: c.alerts }, { key: "messages", label: "患者消息", count: c.messages }, { key: "answered", label: "患者已回答", count: c.answered }, { key: "due", label: "待随访", count: c.due }]} />
        <div className="p-3">
          {tab === "alerts" && (data.alerts.length === 0 ? <Empty title="没有未处理的预警" /> : <ul className="space-y-2">{data.alerts.map((a: any) => (
            <li key={a.id} className={`rounded-xl border p-3 ${a.level === "high_risk" ? "border-danger-200 bg-danger-50/40" : "border-amber-200 bg-amber-50/30"}`}>
              <div className="flex flex-wrap items-center gap-2"><StatusChip s={a.level} /><span className="font-medium text-ink-900">{a.patient_name}</span><span className="text-xs text-ink-400">{ago(a.created_at)}</span>
                <span className="ml-auto flex gap-1.5">{can("followup.reply") && <button className="btn-secondary btn-sm" onClick={() => setChat({ patient_id: a.patient_id, name: a.patient_name, encounter_id: a.encounter_id })}><MessageSquareReply size={13} />联系患者</button>}
                  {can("followup.reply") && <button className="btn-primary btn-sm" onClick={() => setAlert(a)}>处理</button>}
                  <Link className="btn-secondary btn-sm" href={`/encounters/${a.encounter_id}`}><Stethoscope size={13} />原就诊</Link></span></div>
              <div className="mt-1.5 text-[13px] text-ink-700">{(a.rules || []).map((r: any) => r.detail).join("；") || "—"}</div></li>))}</ul>)}
          {tab === "messages" && (data.unread_messages.length === 0 ? <Empty title="没有未读的患者消息" /> : <ul className="divide-y divide-ink-50">{data.unread_messages.map((m: any) => (
            <li key={m.id} className="flex items-center gap-3 py-2.5"><div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="font-medium">{m.patient_name}</span><span className="text-xs text-ink-400">{ago(m.created_at)}</span></div><div className="truncate text-[13px] text-ink-600">{m.body}</div></div>
              <button className="btn-primary btn-sm" onClick={() => setChat({ patient_id: m.patient_id, name: m.patient_name, encounter_id: m.encounter_id })}>回复</button></li>))}</ul>)}
          {tab === "answered" && (data.answered.length === 0 ? <Empty title="没有待查看的回答" /> : <TaskList items={data.answered} onOpen={setTask} />)}
          {tab === "due" && (data.due.length === 0 ? <Empty title="今天没有到期的随访" /> : <TaskList items={data.due} onOpen={setTask} />)}
        </div>
      </div>
      {task && <TaskModal id={task.id} onClose={() => { setTask(null); mutate(); }} onChat={(p) => { setTask(null); setChat(p); }} />}
      {chat && <ChatModal ctx={chat} onClose={() => { setChat(null); mutate(); }} />}
      {alert && <AlertModal a={alert} onClose={() => { setAlert(null); mutate(); }} />}
    </div>
  );
}

function TaskList({ items, onOpen }: { items: any[]; onOpen: (t: any) => void }) {
  return <ul className="divide-y divide-ink-50">{items.map((t) => (
    <li key={t.id}><button className="flex w-full items-center gap-3 px-1 py-2.5 text-left hover:bg-ink-50/60" onClick={() => onOpen(t)}><span className="font-medium">{t.patient_name}</span><span className="tag-neutral">第 {t.seq} 次 · {KIND[t.kind] || t.kind}</span>
      <span className="text-xs text-ink-400">应于 {fmtDay(t.due_at)}</span>{t.due_state === "overdue" && <span className="tag-err">已逾期</span>}<span className="ml-auto"><StatusChip s={t.status} /></span></button></li>))}</ul>;
}

function TaskModal({ id, onClose, onChat }: { id: string; onClose: () => void; onChat: (p: any) => void }) {
  const { can } = useAuth(); const act = useAction();
  const { data, error, mutate } = useSWR(`/followup-tasks/${id}`);
  const t = data?.task;
  return (
    <Modal open onClose={onClose} width="max-w-2xl" title={t ? `${t.patient_name} · 第 ${t.seq} 次随访` : "随访详情"}
      footer={<>{t && can("followup.reply") && <button className="btn-secondary mr-auto" onClick={() => onChat({ patient_id: t.patient_id, name: t.patient_name, encounter_id: t.encounter_id, task_id: t.id })}><MessageSquareReply size={14} />联系患者</button>}
        {t && can("followup.write") && t.status === "answered" && <button className="btn-primary" disabled={act.any} onClick={() => act.run("close", async () => { await api.post(`/followup-tasks/${id}/close`); onClose(); }, { ok: "已标记处理" })}><Check size={14} />标记已处理</button>}<button className="btn-secondary" onClick={onClose}>关闭</button></>}>
      {error ? <ErrorBox error={error} onRetry={() => mutate()} /> : !data ? <Skeleton className="h-40 w-full" /> : (
        <div className="space-y-4">
          <div><div className="mb-1 text-xs font-semibold text-ink-500">询问的问题</div><ul className="list-disc space-y-0.5 pl-5 text-[13px] text-ink-700">{t.questions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ul></div>
          {data.responses.length === 0 ? <Banner kind="info">患者尚未回答。</Banner> : data.responses.map((r: any) => (
            <div key={r.id} className={`rounded-xl border p-3 ${r.risk_level === "high_risk" ? "border-danger-200 bg-danger-50/40" : r.risk_level === "attention" ? "border-amber-200 bg-amber-50/30" : "border-ink-100"}`}>
              <div className="flex items-center gap-2 text-xs text-ink-500"><StatusChip s={r.risk_level} /><span>{fmtTime(r.submitted_at)}</span><span className="ml-auto text-[11px] text-ink-300">规则判定：{r.ai_summary?.rules_version}</span></div>
              {Object.entries(r.answers || {}).map(([k, v]) => <div key={k} className="mt-1.5 text-[13px]"><span className="text-ink-400">{k === "symptom_trend" ? "症状变化" : k}：</span>{String(v)}</div>)}
              {r.free_text && <div className="mt-1.5 whitespace-pre-wrap rounded bg-white/70 p-2 text-[13px] text-ink-800">{r.free_text}</div>}
              {r.rule_hits.length > 0 && <div className="mt-2 text-xs text-ink-600"><b>命中规则：</b>{r.rule_hits.map((h: any) => h.detail).join("；")}</div>}
              {r.ai_summary?.recommended_action && <div className="mt-1 text-xs font-medium text-danger-700">建议：{r.ai_summary.recommended_action}</div>}</div>))}
          {data.messages.length > 0 && <div><div className="mb-1 text-xs font-semibold text-ink-500">往来消息</div><Bubbles items={data.messages} /></div>}
        </div>)}
    </Modal>);
}

function Bubbles({ items }: { items: any[] }) {
  return <ul className="space-y-1.5">{items.map((m) => <li key={m.id} className={`flex ${m.sender_kind === "patient" ? "justify-start" : "justify-end"}`}><div className={`max-w-[80%] rounded-2xl px-3 py-1.5 text-[13px] ${m.sender_kind === "patient" ? "bg-ink-100 text-ink-800" : "bg-brand-600 text-white"}`}><div className="whitespace-pre-wrap">{m.body}</div><div className={`mt-0.5 text-[10px] ${m.sender_kind === "patient" ? "text-ink-400" : "text-white/70"}`}>{m.sender_name} · {fmtTime(m.created_at)}</div></div></li>)}</ul>;
}

function ChatModal({ ctx, onClose }: { ctx: { patient_id: string; name: string; encounter_id?: string; task_id?: string }; onClose: () => void }) {
  const act = useAction(); const { data, mutate } = useSWR(`/patients/${ctx.patient_id}/messages`, { refreshInterval: 10000 });
  const [body, setBody] = useState(""); const [key, setKey] = useState(newKey());
  const send = () => act.run("send", async () => { await api.post(`/patients/${ctx.patient_id}/messages`, { body: body.trim(), task_id: ctx.task_id, encounter_id: ctx.encounter_id }, { idempotencyKey: key }); setBody(""); setKey(newKey()); await mutate(); });
  return (
    <Modal open onClose={onClose} title={`与 ${ctx.name} 的消息`} footer={<button className="btn-secondary" onClick={onClose}>关闭</button>}>
      <div className="max-h-72 overflow-auto pr-1">{!data ? <Skeleton className="h-24 w-full" /> : data.items.length === 0 ? <div className="py-6 text-center text-sm text-ink-400">还没有消息</div> : <Bubbles items={data.items} />}</div>
      <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (body.trim()) send(); }}><input className="input" placeholder="回复患者…（患者在随访页面看到）" value={body} onChange={(e) => setBody(e.target.value)} maxLength={2000} aria-label="消息内容" /><button className="btn-primary" disabled={act.any || !body.trim()}>{act.is("send") ? <Spinner size={14} /> : <Send size={14} />}发送</button></form>
      <p className="mt-2 text-[11px] text-ink-400">消息不能替代急诊处置；如患者描述危急情况，请电话联系或建议立即就医。</p>
    </Modal>);
}

function AlertModal({ a, onClose }: { a: any; onClose: () => void }) {
  const act = useAction(); const [status, setStatus] = useState("acknowledged"); const [note, setNote] = useState("");
  const high = a.level === "high_risk"; const need = status === "resolved" && high && note.trim().length < 4;
  return (
    <Modal open onClose={onClose} title={`处理预警 · ${a.patient_name}`} width="max-w-md" footer={<><button className="btn-secondary" onClick={onClose}>取消</button><button className="btn-primary" disabled={act.any || need} onClick={() => act.run("h", async () => { await api.post(`/followup-alerts/${a.id}/handle`, { status, note: note.trim() }); onClose(); }, { ok: "已记录处理" })}>{act.is("h") ? <Spinner size={14} /> : null}确定</button></>}>
      <div className="mb-3 flex items-center gap-2"><StatusChip s={a.level} /><span className="text-[13px] text-ink-700">{(a.rules || []).map((r: any) => r.detail).join("；")}</span></div>
      <div className="space-y-2"><label className="flex items-center gap-2 text-sm"><input type="radio" className="accent-brand-600" checked={status === "acknowledged"} onChange={() => setStatus("acknowledged")} />已知悉，正在处理（预警保持关注）</label>
        <label className="flex items-center gap-2 text-sm"><input type="radio" className="accent-brand-600" checked={status === "resolved"} onChange={() => setStatus("resolved")} />已处置完毕{high && <span className="text-xs text-danger-600">（高危预警必须写处置说明）</span>}</label></div>
      <textarea className="input mt-3 min-h-[84px]" placeholder={high ? "处置说明：如已电话联系、建议急诊、已安排复诊…" : "备注（可选）"} value={note} onChange={(e) => setNote(e.target.value)} aria-label="处置说明" />
      {need && <div className="mt-1 text-xs text-danger-600">请至少写 4 个字的处置说明</div>}
    </Modal>);
}
