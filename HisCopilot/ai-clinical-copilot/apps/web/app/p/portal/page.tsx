"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ClipboardList, MessageCircle, Phone, RefreshCw, Send } from "lucide-react";
import { api, ApiError, errMsg, fmtDay, fmtTime, newKey } from "@/lib/api";
import { Spinner } from "@/components/ui";

const KEY = "xy_portal_token";
const TREND = ["明显好转", "好转", "无变化", "加重"];

export default function Portal() {
  const [token, setToken] = useState(""); const [data, setData] = useState<any>(null); const [fatal, setFatal] = useState("");
  const load = useCallback(async (t: string) => { try { setData(await api.get("/p/portal", { token: t })); setFatal(""); } catch (e) { setFatal(e instanceof ApiError && e.status === 0 ? "网络连接失败，请检查网络后刷新。" : "链接无效或已过期，请联系诊所重新获取。"); } }, []);
  useEffect(() => {
    let t = sessionStorage.getItem(KEY) || ""; const m = /(?:^#|&)t=([^&]+)/.exec(location.hash);
    if (m) { t = decodeURIComponent(m[1]); sessionStorage.setItem(KEY, t); history.replaceState(null, "", location.pathname); }
    if (!t) { setFatal("没有找到访问凭证，请使用诊所发给您的链接或二维码。"); return; }
    setToken(t); load(t);
  }, [load]);
  useEffect(() => { if (!token || fatal) return; const i = setInterval(() => load(token), 20000); return () => clearInterval(i); }, [token, fatal, load]);

  if (fatal) return <div className="card card-pad mt-6 text-center"><div className="mx-auto grid h-14 w-14 place-items-center rounded-2xl bg-amber-50 text-amber-600"><AlertTriangle size={26} /></div><p className="mt-4 text-sm leading-relaxed text-ink-600">{fatal}</p><button className="btn-secondary mt-5" onClick={() => location.reload()}><RefreshCw size={14} />刷新</button></div>;
  if (!data) return <div className="grid h-[50vh] place-items-center text-ink-300"><Spinner size={26} /></div>;
  return (
    <div className="space-y-4">
      <div className="px-1"><h1 className="text-xl font-semibold text-ink-950">{data.patient.name}，您好</h1><p className="mt-1 text-[13px] text-ink-500">{data.clinic} · 随访与留言。本页只显示您本人的信息。</p></div>
      <div className="flex items-start gap-2 rounded-xl border border-danger-100 bg-danger-50/70 p-3 text-[13px] text-danger-700"><Phone size={15} className="mt-0.5 shrink-0" /><span>如出现<b>胸痛、呼吸困难、意识不清、大出血、严重过敏</b>等紧急情况，请立即拨打 <b>120</b> 或前往急诊，不要等待线上回复。</span></div>

      {data.tasks.length > 0 && data.tasks.map((t: any) => <Task key={t.id} t={t} token={token} onDone={() => load(token)} />)}
      {data.tasks.length === 0 && <div className="card card-pad flex items-center gap-3 text-sm text-ink-600"><CheckCircle2 className="text-jade-600" size={20} />目前没有需要您回答的随访。</div>}

      {data.care_summaries.length > 0 && (
        <section className="card card-pad space-y-3"><h2 className="flex items-center gap-2 text-[15px] font-semibold text-ink-900"><ClipboardList size={17} className="text-brand-600" />医生的医嘱摘要</h2>
          {data.care_summaries.map((c: any) => (
            <div key={c.encounter_no + c.date} className="rounded-xl bg-ink-50 p-3 text-[13.5px]"><div className="text-xs text-ink-400">{fmtDay(c.date)} · 就诊号 {c.encounter_no}</div>
              <p className="mt-1 whitespace-pre-wrap leading-relaxed text-ink-800">{c.summary.summary}</p>
              {(c.summary.advice || []).length > 0 && <ul className="mt-2 list-disc space-y-0.5 pl-5 text-ink-700">{c.summary.advice.map((a: string, i: number) => <li key={i}>{a}</li>)}</ul>}
              {c.summary.revisit && <div className="mt-2 text-ink-700"><b>复诊建议：</b>{c.summary.revisit}</div>}</div>))}</section>)}

      <Messages items={data.messages} token={token} onSent={() => load(token)} />
    </div>
  );
}

function Task({ t, token, onDone }: { t: any; token: string; onDone: () => void }) {
  const [trend, setTrend] = useState(""); const [ans, setAns] = useState<Record<string, string>>({}); const [free, setFree] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const [res, setRes] = useState<{ message: string; urgent: boolean } | null>(null); const key = useRef(newKey());
  const submit = async () => {
    if (!trend && !free.trim() && !Object.values(ans).some((v) => v.trim())) return setErr("请至少回答一项，或写几句话描述您现在的情况");
    setBusy(true); setErr("");
    try { const r = await api.post(`/p/portal/tasks/${t.id}/respond`, { answers: Object.fromEntries(Object.entries(ans).filter(([, v]) => v.trim())), free_text: free.trim(), symptom_trend: trend }, { token, idempotencyKey: key.current }); setRes({ message: r.message, urgent: r.urgent }); }
    catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  if (res) return <div className={`card card-pad ${res.urgent ? "border-danger-200 bg-danger-50/60" : "border-jade-100 bg-jade-50/50"}`}><div className="flex items-start gap-3">{res.urgent ? <AlertTriangle className="mt-0.5 shrink-0 text-danger-600" size={22} /> : <CheckCircle2 className="mt-0.5 shrink-0 text-jade-600" size={22} />}<div><div className="font-semibold text-ink-900">{res.urgent ? "已收到，请注意" : "已提交，谢谢"}</div><p className="mt-1 text-sm leading-relaxed text-ink-700">{res.message}</p><button className="btn-ghost btn-sm mt-2" onClick={onDone}>刷新</button></div></div></div>;
  return (
    <section className="card card-pad space-y-4"><div><h2 className="text-[15px] font-semibold text-ink-900">随访问题</h2><p className="mt-0.5 text-xs text-ink-400">应答日期 {fmtDay(t.due_at)}。如实回答，医生会据此判断是否需要调整治疗。</p></div>
      <div><div className="mb-1.5 text-[13px] font-medium text-ink-700">症状与上次就诊相比：</div><div className="grid grid-cols-4 gap-2">{TREND.map((x) => <button key={x} type="button" aria-pressed={trend === x} onClick={() => setTrend(trend === x ? "" : x)} className={`rounded-lg border py-2 text-[13px] font-medium transition ${trend === x ? (x === "加重" ? "border-danger-500 bg-danger-50 text-danger-700" : "border-brand-500 bg-brand-50 text-brand-700") : "border-ink-200 bg-white text-ink-600"}`}>{x}</button>)}</div></div>
      {(t.questions || []).map((q: string) => <div key={q}><label className="mb-1 block text-[13px] font-medium text-ink-700">{q}</label><textarea className="input min-h-[56px]" value={ans[q] || ""} onChange={(e) => setAns({ ...ans, [q]: e.target.value })} maxLength={500} /></div>)}
      <div><label className="mb-1 block text-[13px] font-medium text-ink-700">还有什么想告诉医生的？（选填）</label><textarea className="input min-h-[72px]" value={free} onChange={(e) => setFree(e.target.value)} maxLength={3000} /></div>
      {err && <div role="alert" className="rounded-lg border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-600">{err}</div>}
      <button className="btn-primary btn-lg w-full" disabled={busy} onClick={submit}>{busy ? <Spinner size={16} /> : <Send size={16} />}提交回答</button></section>);
}

function Messages({ items, token, onSent }: { items: any[]; token: string; onSent: () => void }) {
  const [body, setBody] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState(""); const key = useRef(newKey());
  const send = async () => {
    if (!body.trim()) return; setBusy(true); setErr("");
    try { await api.post("/p/portal/messages", { body: body.trim() }, { token, idempotencyKey: key.current }); key.current = newKey(); setBody(""); onSent(); } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <section className="card card-pad space-y-3"><h2 className="flex items-center gap-2 text-[15px] font-semibold text-ink-900"><MessageCircle size={17} className="text-brand-600" />给诊所留言</h2>
      {items.length > 0 && <ul className="max-h-72 space-y-2 overflow-auto pr-1">{items.map((m) => <li key={m.id} className={`flex ${m.sender_kind === "patient" ? "justify-end" : "justify-start"}`}><div className={`max-w-[85%] rounded-2xl px-3 py-2 text-[13.5px] ${m.sender_kind === "patient" ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-800"}`}><div className="whitespace-pre-wrap">{m.body}</div><div className={`mt-0.5 text-[10px] ${m.sender_kind === "patient" ? "text-white/70" : "text-ink-400"}`}>{m.sender_kind === "patient" ? "我" : m.sender_name} · {fmtTime(m.created_at)}</div></div></li>)}</ul>}
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); send(); }}><input className="input" placeholder="写下想问医生的话…" value={body} onChange={(e) => setBody(e.target.value)} maxLength={2000} aria-label="留言内容" /><button className="btn-primary shrink-0" disabled={busy || !body.trim()}>{busy ? <Spinner size={15} /> : <Send size={15} />}发送</button></form>
      {err && <div className="text-xs text-danger-600">{err}</div>}<p className="text-[11px] text-ink-400">留言不是即时通讯，诊所工作时间内回复；紧急情况请拨打 120。</p></section>);
}
