"use client";
import { use, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { ArrowLeft, CalendarPlus, FileText, Plus, ShieldAlert, Stethoscope, Trash2 } from "lucide-react";
import { Banner, Empty, ErrorBox, Field, Modal, Skeleton, StatusChip, useConfirm, useToast } from "@/components/ui";
import { api, errMsg, fmtDay, fmtTime } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function PatientDetail({ params }: { params: Promise<{ pid: string }> }) {
  const { pid } = use(params);
  const router = useRouter(); const toast = useToast(); const confirm = useConfirm(); const { can } = useAuth();
  const { data, error, isLoading, mutate } = useSWR(`/patients/${pid}`);
  const recs = useSWR(can("record.read") ? `/records?patient_id=${pid}&limit=50` : null);
  const rxs = useSWR(can("rx.read") && can("encounter.read") ? `/prescriptions?patient_id=${pid}&limit=50` : null);
  const [alg, setAlg] = useState(false); const [newAlg, setNewAlg] = useState("");
  if (error) return <ErrorBox error={error} onRetry={() => mutate()} />;
  if (isLoading || !data) return <div className="space-y-4"><Skeleton className="h-8 w-48" /><Skeleton className="h-40 w-full" /></div>;
  const p = data.patient, encs: any[] = data.encounters;
  const recByEnc: Record<string, any> = Object.fromEntries((recs.data?.items || []).map((r: any) => [r.encounter_id, r]));
  const addAllergy = async () => {
    try { await api.post(`/patients/${pid}/allergies`, { allergen: newAlg.trim() }); toast("ok", "已登记过敏史，进行中的就诊将提示重新核对"); setNewAlg(""); setAlg(false); mutate(); } catch (e) { toast("err", errMsg(e)); }
  };
  const rmAllergy = async (a: any) => {
    const r = await confirm({ title: "移除过敏记录", body: `确定移除「${a.allergen}」？历史处方的校验记录不受影响。`, confirmText: "移除", danger: true });
    if (r.ok) { try { await api.del(`/patients/${pid}/allergies/${a.id}`); mutate(); } catch (e) { toast("err", errMsg(e)); } }
  };
  const newEnc = async () => {
    const open = encs.find((e) => ["WAITING", "IN_PROGRESS"].includes(e.status));
    if (open) { const r = await confirm({ title: "该患者已有进行中的就诊", body: `就诊 ${open.encounter_no}（${open.status === "WAITING" ? "待接诊" : "接诊中"}）尚未结束。是否继续该就诊？`, confirmText: "继续该就诊" }); if (r.ok) router.push(`/encounters/${open.id}`); return; }
    router.push(`/encounters/new?pid=${pid}`);
  };
  return (
    <div className="space-y-5">
      <div><Link href="/patients" className="inline-flex items-center gap-1 text-xs text-ink-400 hover:text-brand-600"><ArrowLeft size={13} />患者档案</Link></div>
      <div className="card card-pad flex flex-wrap items-center gap-4">
        <span className="grid h-14 w-14 place-items-center rounded-2xl bg-brand-50 text-xl font-semibold text-brand-700">{p.name.slice(0, 1)}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2"><h1 className="text-xl font-semibold text-ink-950">{p.name}</h1>{p.is_sample && <span className="tag-sim">示例数据</span>}</div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-500"><span>{p.gender || "性别未填写"}</span><span>{p.age != null ? `${p.age} 岁` : "年龄未填写"}</span><span>{p.phone || "手机未填写"}</span><span className="font-mono text-xs">门诊号 {p.mrn}</span></div>
        </div>
        {can("encounter.create") && <button className="btn-primary" onClick={newEnc}><Plus size={16} />新建就诊</button>}
        {can("appointment.manage") && <Link href={`/appointments?pid=${pid}`} className="btn-secondary"><CalendarPlus size={15} />预约</Link>}
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        <section className="card overflow-hidden">
          <div className="border-b border-ink-100 px-4 py-3"><h2 className="h-sec">就诊时间线</h2></div>
          {encs.length === 0 ? <Empty title="暂无就诊记录" action={can("encounter.create") ? <button className="btn-secondary btn-sm mt-1" onClick={newEnc}>新建就诊</button> : undefined} /> :
            <ol className="relative px-4 py-3">{encs.map((e, i) => {
              const rec = recByEnc[e.id];
              return (
                <li key={e.id} className="relative pb-4 pl-6 last:pb-1">
                  <span className="absolute left-[5px] top-2 h-2.5 w-2.5 rounded-full border-2 border-brand-500 bg-white" />{i < encs.length - 1 && <span className="absolute left-[9px] top-5 h-full w-px bg-ink-100" />}
                  <div className="rounded-xl border border-ink-100 p-3 transition hover:border-brand-200 hover:bg-brand-50/30">
                    <div className="flex flex-wrap items-center gap-2"><span className="text-sm font-medium text-ink-900">{fmtTime(e.created_at)}</span><span className="font-mono text-[11px] text-ink-400">{e.encounter_no}</span><StatusChip s={e.status} />{e.visit_type !== "initial" && <span className="tag-neutral">{e.visit_type === "revisit" ? "复诊" : "随访触发"}</span>}{e.source === "patient_intake" && <span className="tag-brand">患者自填</span>}</div>
                    <div className="mt-1 text-sm text-ink-600">{e.chief_complaint || <span className="text-ink-300">主诉未填写</span>}</div>
                    <div className="mt-2.5 flex flex-wrap gap-2">
                      <Link className="btn-secondary btn-sm" href={`/encounters/${e.id}`}><Stethoscope size={13} />{e.status === "COMPLETED" ? "查看本次就诊" : "进入诊疗室"}</Link>
                      {rec && <Link className="btn-secondary btn-sm" href={`/records/${rec.id}`}><FileText size={13} />病历 {rec.status === "finalized" ? "（已定稿）" : "（草稿）"}</Link>}
                    </div></div>
                </li>);
            })}</ol>}
        </section>
        <aside className="space-y-5">
          <section className="card card-pad">
            <div className="mb-2 flex items-center gap-2"><ShieldAlert size={16} className="text-danger-500" /><h2 className="h-sec">过敏史</h2>{can("patient.write") && <button className="btn-ghost btn-sm ml-auto" onClick={() => setAlg(true)}><Plus size={13} />登记</button>}</div>
            {p.allergies.length === 0 ? <Banner kind="warn" title="尚未登记过敏史">开药前请先向患者核实；系统不会假定“无过敏”。</Banner> :
              <ul className="space-y-1.5">{p.allergies.map((a: any) => (
                <li key={a.id} className="flex items-center gap-2 rounded-lg bg-danger-50/60 px-2.5 py-1.5 text-sm"><span className="font-medium text-danger-700">{a.allergen}</span>{a.severity && <span className="text-xs text-danger-500">{a.severity}</span>}<span className={`ml-auto text-[10px] ${a.confirmed ? "text-jade-600" : "text-ink-400"}`}>{a.confirmed ? "医师已核实" : "患者自述"}</span>
                  {can("patient.write") && <button onClick={() => rmAllergy(a)} className="text-ink-300 hover:text-danger-600"><Trash2 size={13} /></button>}</li>))}</ul>}
          </section>
          <section className="card card-pad text-sm">
            <h2 className="h-sec mb-2">基本信息</h2>
            <dl className="space-y-1.5 text-[13px]">{[["慢性病", (p.chronic_conditions || []).join("、")], ["医保", p.insurance], ["地址", p.address], ["建档时间", fmtDay(p.created_at)]].map(([k, v]) => <div key={k as string} className="flex gap-3"><dt className="w-14 shrink-0 text-ink-400">{k}</dt><dd className="text-ink-700">{v || <span className="text-ink-300">未填写</span>}</dd></div>)}</dl>
          </section>
          {rxs.data?.items?.length > 0 && <section className="card card-pad"><h2 className="h-sec mb-2">处方（{rxs.data.items.length}）</h2><ul className="space-y-1 text-[13px]">{rxs.data.items.slice(0, 6).map((r: any) => <li key={r.id}><Link href={`/prescriptions/${r.id}`} className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-ink-50"><span className="font-mono text-[11px] text-ink-400">{r.rx_no}</span><span className="truncate">{r.title}</span><StatusChip s={r.status} /></Link></li>)}</ul></section>}
        </aside>
      </div>
      <Modal open={alg} onClose={() => setAlg(false)} title="登记过敏史" width="max-w-sm" footer={<><button className="btn-secondary" onClick={() => setAlg(false)}>取消</button><button className="btn-primary" disabled={!newAlg.trim()} onClick={addAllergy}>登记</button></>}>
        <Field label="过敏原" hint="如：青霉素、头孢、磺胺、海鲜。医师登记视为已核实。"><input className="input" value={newAlg} onChange={(e) => setNewAlg(e.target.value)} autoFocus onKeyDown={(e) => e.key === "Enter" && newAlg.trim() && addAllergy()} /></Field>
      </Modal>
    </div>
  );
}
