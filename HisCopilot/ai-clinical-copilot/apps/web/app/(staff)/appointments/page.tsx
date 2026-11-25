"use client";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { CalendarPlus, ChevronLeft, ChevronRight, LogIn, Search, X } from "lucide-react";
import { api, errMsg, fmtTime, newKey } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Empty, ErrorBox, Field, Modal, SkeletonList, Spinner, StatusChip, useConfirm, useDebounced, useToast } from "@/components/ui";
import { useAction } from "@/lib/useAction";

const ymd = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const localInput = (d: Date) => `${ymd(d)}T${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

function Page() {
  const sp = useSearchParams(); const router = useRouter(); const { can, user } = useAuth(); const act = useAction(); const confirm = useConfirm(); const toast = useToast();
  const [day, setDay] = useState(ymd(new Date()));
  const { data, error, isLoading, mutate } = useSWR(`/appointments?day=${day}`, { refreshInterval: 30000 });
  const [open, setOpen] = useState(!!sp.get("pid")); const pid = sp.get("pid") || ""; const source = sp.get("source") || "";
  const shift = (n: number) => { const d = new Date(day + "T00:00:00"); d.setDate(d.getDate() + n); setDay(ymd(d)); };
  const checkIn = (a: any) => act.run(`ci-${a.id}`, async () => { const r = await api.post(`/appointments/${a.id}/check-in`, {}, { idempotencyKey: newKey() }); toast("ok", r.reused ? "已到诊，进入原就诊" : "到诊登记完成，已生成待接诊记录"); router.push(`/encounters/${r.encounter.id}`); }, { onStale: () => mutate() });
  const cancel = async (a: any) => { const c = await confirm({ title: "取消预约？", body: `${a.patient_name} ${fmtTime(a.slot_start, false)}`, confirmText: "取消预约", danger: true, reason: { label: "原因（可选）", min: 0 } }); if (c.ok) act.run(`cx-${a.id}`, async () => { await api.post(`/appointments/${a.id}/cancel?reason=${encodeURIComponent(c.reason)}`); await mutate(); }, { ok: "已取消" }); };
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="h-page">预约挂号</h1><p className="mt-1 text-sm text-ink-500">到诊登记会生成“待接诊”的就诊；重复点击只会进入同一个就诊。</p></div>
        <button className="btn-primary" onClick={() => setOpen(true)}><CalendarPlus size={16} />新建预约</button></div>
      <div className="card overflow-hidden">
        <div className="flex items-center gap-2 border-b border-ink-100 p-3"><button className="btn-secondary btn-sm" aria-label="前一天" onClick={() => shift(-1)}><ChevronLeft size={14} /></button>
          <input type="date" className="input w-auto py-1.5" value={day} onChange={(e) => e.target.value && setDay(e.target.value)} aria-label="日期" /><button className="btn-secondary btn-sm" aria-label="后一天" onClick={() => shift(1)}><ChevronRight size={14} /></button><button className="btn-ghost btn-sm" onClick={() => setDay(ymd(new Date()))}>今天</button>
          <span className="ml-auto text-xs text-ink-400">{data ? `${data.items.length} 个预约` : ""}</span></div>
        {error ? <div className="p-4"><ErrorBox error={error} onRetry={() => mutate()} /></div> : isLoading ? <SkeletonList rows={4} /> : !data?.items.length ? <Empty title="这一天没有预约" /> : (
          <ul className="divide-y divide-ink-50">{data.items.map((a: any) => (
            <li key={a.id} className="flex flex-wrap items-center gap-3 px-4 py-3"><div className="w-16 text-[15px] font-semibold tabular-nums text-ink-900">{fmtTime(a.slot_start, false)}</div>
              <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="font-medium">{a.patient_name}</span>{!a.patient_id && <span className="tag-warn">未建档</span>}{a.kind === "followup_visit" && <span className="tag-brand">随访复诊</span>}</div><div className="text-xs text-ink-500">{a.doctor_name}{a.reason ? ` · ${a.reason}` : ""}{a.guest_phone ? ` · ${a.guest_phone}` : ""}</div></div>
              <StatusChip s={a.status} />
              {a.status === "booked" && can("encounter.create") && <button className="btn-primary btn-sm" disabled={act.any} onClick={() => checkIn(a)}>{act.is(`ci-${a.id}`) ? <Spinner size={12} /> : <LogIn size={13} />}到诊登记</button>}
              {a.status === "booked" && <button className="btn-ghost btn-sm text-ink-400" disabled={act.any} onClick={() => cancel(a)}><X size={13} />取消</button>}
              {a.status === "checked_in" && a.encounter_id && <button className="btn-secondary btn-sm" onClick={() => router.push(`/encounters/${a.encounter_id}`)}>进入就诊</button>}</li>))}</ul>)}
      </div>
      {open && <NewAppt initialPid={pid} source={source} day={day} onClose={() => { setOpen(false); if (sp.get("pid")) router.replace("/appointments"); }} onDone={(d) => { setOpen(false); setDay(d); mutate(); if (sp.get("pid")) router.replace("/appointments"); }} />}
    </div>
  );
}

function NewAppt({ initialPid, source, day, onClose, onDone }: { initialPid: string; source: string; day: string; onClose: () => void; onDone: (day: string) => void }) {
  const { user } = useAuth(); const act = useAction(); const [err, setErr] = useState("");
  const docs = useSWR("/doctors"); const [q, setQ] = useState(""); const dq = useDebounced(q, 250); const [sel, setSel] = useState<any>(null); const [guest, setGuest] = useState({ name: "", phone: "" });
  const found = useSWR(dq.trim() && !sel ? `/patients?limit=6&q=${encodeURIComponent(dq.trim())}` : null); const pre = useSWR(initialPid ? `/patients/${initialPid}` : null);
  useEffect(() => { if (pre.data?.patient) setSel(pre.data.patient); }, [pre.data]);
  const [doctor, setDoctor] = useState(user?.role === "DOCTOR" ? user.id : ""); const [at, setAt] = useState(() => { const d = new Date(day + "T09:00:00"); if (ymd(d) === ymd(new Date())) { const n = new Date(); n.setMinutes(Math.ceil((n.getMinutes() + 1) / 10) * 10, 0, 0); return localInput(n); } return localInput(d); });
  const [dur, setDur] = useState(20); const [reason, setReason] = useState("");
  const submit = () => {
    setErr(""); if (!sel && !guest.name.trim()) return setErr("请选择已有患者，或填写预约人姓名"); if (!doctor) return setErr("请选择医师"); if (!at) return setErr("请选择时间");
    const start = new Date(at);
    if (isNaN(+start)) return setErr("时间格式不正确");
    act.run("create", async () => { try { await api.post("/appointments", { patient_id: sel?.id || null, guest_name: guest.name.trim(), guest_phone: guest.phone.trim(), doctor_id: doctor, slot_start: start.toISOString(), duration_min: dur, reason: reason.trim(), kind: source ? "followup_visit" : "visit", source_encounter_id: source || null }); onDone(ymd(start)); } catch (e) { setErr(errMsg(e)); } }, { ok: "预约已创建", quiet: true });
  };
  return (
    <Modal open onClose={onClose} title="新建预约" footer={<><button className="btn-secondary" onClick={onClose}>取消</button><button className="btn-primary" disabled={act.any} onClick={submit}>{act.is("create") ? <Spinner size={14} /> : null}创建预约</button></>}>
      <div className="space-y-3.5">
        {sel ? <div className="flex items-center gap-2 rounded-xl border border-brand-100 bg-brand-50/50 p-2.5"><span className="font-medium">{sel.name}</span><span className="text-xs text-ink-500">{sel.mrn}</span>{!initialPid && <button className="btn-ghost btn-sm ml-auto" onClick={() => setSel(null)}>更换</button>}</div> : (<>
          <div className="relative"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" /><input className="input pl-8" placeholder="搜索已有患者：姓名 / 手机 / 门诊号" value={q} onChange={(e) => setQ(e.target.value)} autoFocus /></div>
          {found.data?.items.length > 0 && <ul className="divide-y divide-ink-50 rounded-lg border border-ink-100">{found.data.items.map((p: any) => <li key={p.id}><button className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-brand-50/40" onClick={() => setSel(p)}><b>{p.name}</b><span className="text-xs text-ink-400">{p.phone || "无手机"} · {p.mrn}</span></button></li>)}</ul>}
          <div className="rounded-lg bg-ink-50 p-2.5"><div className="mb-1.5 text-xs text-ink-500">或登记为新来访者（到诊时再建档）</div><div className="grid gap-2 sm:grid-cols-2"><input className="input" placeholder="姓名" value={guest.name} onChange={(e) => setGuest({ ...guest, name: e.target.value })} aria-label="预约人姓名" /><input className="input" inputMode="numeric" placeholder="手机" value={guest.phone} onChange={(e) => setGuest({ ...guest, phone: e.target.value })} aria-label="预约人手机" /></div></div></>)}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="医师" required><select className="input" value={doctor} onChange={(e) => setDoctor(e.target.value)}><option value="">请选择</option>{docs.data?.items.map((d: any) => <option key={d.id} value={d.id}>{d.name} · {d.dept}</option>)}</select></Field>
          <Field label="时长"><select className="input" value={dur} onChange={(e) => setDur(+e.target.value)}>{[10, 15, 20, 30, 45, 60].map((n) => <option key={n} value={n}>{n} 分钟</option>)}</select></Field>
          <div className="sm:col-span-2"><Field label="就诊时间" required><input className="input" type="datetime-local" step={600} value={at} onChange={(e) => setAt(e.target.value)} /></Field></div>
        </div>
        <Field label="就诊事由（可选）"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="如：复诊、头痛 3 天" /></Field>
        {source && <div className="text-xs text-brand-700">这是对上次就诊的复诊预约，到诊时会自动关联上次就诊。</div>}
        {err && <div role="alert" className="rounded-lg border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-600">{err}</div>}
      </div>
    </Modal>);
}
export default function AppointmentsPage() { return <Suspense><Page /></Suspense>; }
