"use client";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { ArrowLeft, Search, Stethoscope } from "lucide-react";
import { Banner, Empty, Field, Spinner, useDebounced, useToast } from "@/components/ui";
import { NewPatient } from "@/components/NewPatient";
import { api, errMsg, newKey } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function Form() {
  const sp = useSearchParams(); const router = useRouter(); const toast = useToast(); const { user } = useAuth();
  const pid = sp.get("pid") || ""; const prev = sp.get("prev") || "";
  const [q, setQ] = useState(""); const dq = useDebounced(q, 250);
  const [sel, setSel] = useState<any>(null);
  const pre = useSWR(pid ? `/patients/${pid}` : null);
  const found = useSWR(dq.trim().length >= 1 && !sel ? `/patients?limit=8&q=${encodeURIComponent(dq.trim())}` : null);
  const docs = useSWR("/doctors");
  const [doctor, setDoctor] = useState(user?.role === "DOCTOR" ? user.id : "");
  const [cc, setCc] = useState(""); const [hpi, setHpi] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const [newP, setNewP] = useState(false); const [key] = useState(newKey());
  useEffect(() => { if (pre.data?.patient && !sel) setSel(pre.data.patient); }, [pre.data, sel]);
  useEffect(() => { if (user?.role === "DOCTOR") setDoctor(user.id); }, [user]);

  const go = async () => {
    if (!sel) return setErr("请先选择或新建患者");
    if (!cc.trim()) return setErr("请填写主诉（可简短）");
    setBusy(true); setErr("");
    try {
      const r = await api.post("/encounters", { patient_id: sel.id, doctor_id: doctor || null, chief_complaint: cc.trim(), hpi: hpi.trim(), prev_encounter_id: prev || null,
        visit_type: prev ? "revisit" : "initial" }, { idempotencyKey: key });
      if (r.reused) toast("info", "刚刚已为该患者创建过就诊，已为您打开");
      router.replace(`/encounters/${r.encounter.id}`);
    } catch (e) { setErr(errMsg(e)); setBusy(false); }
  };
  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <div><button onClick={() => router.back()} className="inline-flex items-center gap-1 text-xs text-ink-400 hover:text-brand-600"><ArrowLeft size={13} />返回</button><h1 className="h-page mt-1">新建接诊</h1><p className="mt-1 text-sm text-ink-500">先确定“谁”，再确定“主诉”。语音、检验单、舌象可在诊疗室中继续补充。</p></div>
      <section className="card card-pad space-y-4">
        <h2 className="h-sec">1 · 患者</h2>
        {sel ? (
          <div className="flex items-center gap-3 rounded-xl border border-brand-100 bg-brand-50/50 p-3">
            <span className="grid h-10 w-10 place-items-center rounded-full bg-brand-100 font-semibold text-brand-700">{sel.name.slice(0, 1)}</span>
            <div className="min-w-0 flex-1"><div className="font-medium">{sel.name} <span className="ml-1 text-xs font-normal text-ink-400">{sel.gender || "性别未填"} · {sel.age != null ? `${sel.age}岁` : "年龄未填"} · {sel.mrn}</span></div>
              <div className="text-xs text-ink-500">过敏史：{sel.allergies?.length ? sel.allergies.map((a: any) => a.allergen).join("、") : <b className="text-amber-600">尚未登记（接诊时请核实）</b>}</div></div>
            {!pid && <button className="btn-ghost btn-sm" onClick={() => setSel(null)}>更换</button>}
          </div>) : (
          <>
            <div className="relative"><Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-300" /><input className="input pl-9" placeholder="搜索已有患者：姓名 / 手机 / 门诊号" value={q} onChange={(e) => setQ(e.target.value)} autoFocus /></div>
            {found.isLoading && <div className="flex justify-center py-3 text-ink-300"><Spinner /></div>}
            {found.data && (found.data.items.length === 0 ? <Empty title="没有找到匹配的患者" action={<button className="btn-primary btn-sm mt-1" onClick={() => setNewP(true)}>新建患者「{dq}」</button>} /> :
              <ul className="divide-y divide-ink-50 rounded-xl border border-ink-100">{found.data.items.map((p: any) => (
                <li key={p.id}><button className="flex w-full items-center gap-3 px-3 py-2.5 text-left hover:bg-brand-50/40" onClick={() => setSel(p)}><span className="grid h-8 w-8 place-items-center rounded-full bg-ink-100 text-xs font-semibold">{p.name.slice(0, 1)}</span><span className="font-medium">{p.name}</span><span className="text-xs text-ink-400">{p.gender || "—"} · {p.age ?? "—"}岁 · {p.phone || "无手机"}</span><span className="ml-auto font-mono text-[11px] text-ink-300">{p.mrn}</span></button></li>))}</ul>)}
            {!found.data && !found.isLoading && <button className="btn-secondary btn-sm" onClick={() => setNewP(true)}>找不到？新建患者</button>}
          </>)}
      </section>
      <section className="card card-pad space-y-4">
        <h2 className="h-sec">2 · 本次就诊</h2>
        {prev && <Banner kind="info">这是一次<b>复诊</b>，将关联上次就诊记录。</Banner>}
        <Field label="主诉" required hint="主要症状 + 持续时间，如“头痛 3 天，伴头晕”"><input className="input" value={cc} onChange={(e) => setCc(e.target.value)} /></Field>
        <Field label="现病史（可选）"><textarea className="input min-h-[96px]" value={hpi} onChange={(e) => setHpi(e.target.value)} placeholder="发病经过、症状特点、加重/缓解因素、诊治经过…（可稍后在诊疗室补充或语音录入）" /></Field>
        {user?.role !== "DOCTOR" && <Field label="接诊医师" hint="不选则进入待接诊池，由医师认领"><select className="input" value={doctor} onChange={(e) => setDoctor(e.target.value)}><option value="">待分配（医师认领）</option>{docs.data?.items.map((d: any) => <option key={d.id} value={d.id}>{d.name} · {d.dept}</option>)}</select></Field>}
      </section>
      {err && <div role="alert" className="rounded-lg border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-600">{err}</div>}
      <div className="flex justify-end gap-2"><button className="btn-secondary" onClick={() => router.back()}>取消</button><button className="btn-primary btn-lg" disabled={busy} onClick={go}>{busy ? <Spinner /> : <Stethoscope size={17} />}创建并进入诊疗室</button></div>
      <NewPatient open={newP} onClose={() => setNewP(false)} onDone={async (id) => { setNewP(false); const r = await api.get(`/patients/${id}`); setSel(r.patient); }} />
    </div>
  );
}
export default function NewEncounterPage() { return <Suspense><Form /></Suspense>; }
