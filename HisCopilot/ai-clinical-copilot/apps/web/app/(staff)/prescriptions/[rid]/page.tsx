"use client";
import { use } from "react";
import Link from "next/link";
import useSWR from "swr";
import { AlertTriangle, ArrowLeft, Check, CheckCircle2, ClipboardCheck, PackageCheck, Printer, Stethoscope, Undo2 } from "lucide-react";
import { api, fmtTime } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Banner, ErrorBox, Skeleton, Spinner, StatusChip, useConfirm } from "@/components/ui";
import { useAction } from "@/lib/useAction";
import { Card } from "@/components/workspace/bits";
import { SEV } from "@/components/workspace/RxItems";

const TYPE: Record<string, string> = { western: "西药处方", patent: "中成药处方", herbal: "中药饮片处方" };

// Read-only prescription sheet (what a pharmacist reviews) + the pharmacy workflow actions. Doctors edit in the workspace.
export default function RxDetail({ params }: { params: Promise<{ rid: string }> }) {
  const { rid } = use(params); const { user, can } = useAuth(); const act = useAction(); const confirm = useConfirm();
  const { data, error, mutate } = useSWR(`/prescriptions/${rid}`, { revalidateOnFocus: true });
  if (error) return <div className="space-y-3"><Link href="/prescriptions" className="inline-flex items-center gap-1 text-xs text-ink-400 hover:text-brand-600"><ArrowLeft size={13} />处方</Link><ErrorBox error={error} onRetry={() => mutate()} /></div>;
  if (!data) return <div className="space-y-3"><Skeleton className="h-16 w-full" /><Skeleton className="h-72 w-full" /></div>;
  const r = data.prescription, events: any[] = data.events, p = r.patient, safety = r.safety || {}, issues: any[] = safety.issues || [];
  const herbal = r.rx_type === "herbal"; const overrides: any[] = r.override_reasons || [];
  const post = (path: string, body: any, ok: string) => act.run(path, async () => { await api.post(`/pharmacy/prescriptions/${rid}/${path}`, body); await mutate(); }, { ok, onStale: () => mutate() });
  const decide = async (approve: boolean) => {
    const c = await confirm({ title: approve ? "审核通过？" : "退回给医师？", body: approve ? "请确认已核对药品、剂量、过敏与相互作用。" : "退回后医师需要修改并重新签署。", confirmText: approve ? "通过" : "退回", danger: !approve, reason: { label: approve ? "审核备注（可选）" : "退回原因（必填）", min: approve ? 0 : 2 } });
    if (c.ok) post("decide", { approve, note: c.reason }, approve ? "已审核通过" : "已退回");
  };
  return (
    <div className="space-y-4">
      <Link href={user?.role === "PHARMACIST" ? "/pharmacy" : "/prescriptions"} className="inline-flex items-center gap-1 text-xs text-ink-400 hover:text-brand-600"><ArrowLeft size={13} />返回</Link>
      <header className="card card-pad flex flex-wrap items-center gap-4">
        <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h1 className="text-xl font-semibold text-ink-950">{r.title}</h1><span className="tag-neutral">{TYPE[r.rx_type]}</span><StatusChip s={r.status} />{r.ai_generated && <span className="tag-brand">AI 草拟（医师已审阅）</span>}{r.his?.simulated && r.his.status === "mock_submitted" && <span className="tag-sim">模拟 HIS · {r.his.ref}</span>}</div>
          <div className="mt-1 text-sm text-ink-500"><span className="font-mono text-xs">{r.rx_no}</span> · 开具 {r.doctor?.name}{r.doctor?.title ? `（${r.doctor.title}）` : ""} · 签署 {fmtTime(r.signed_at)}</div></div>
        <a className="btn-secondary" href={`/api/v1/prescriptions/${rid}/pdf`} target="_blank" rel="noreferrer"><Printer size={14} />打印 / PDF</a>
        {can("encounter.read") && user?.role !== "PHARMACIST" && <Link className="btn-secondary" href={`/encounters/${r.encounter_id}?step=rx`}><Stethoscope size={14} />在诊疗室中打开</Link>}
      </header>

      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          <Card title="处方明细">
            {herbal ? (<>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">{r.items.map((i: any) => <div key={i.line_no} className="rounded-lg border border-ink-100 px-3 py-2 text-sm"><span className="font-medium text-ink-900">{i.name}</span><span className="ml-2 text-ink-700">{i.dose_g} g</span>{i.special && <span className="ml-1 text-xs text-amber-700">（{i.special}）</span>}</div>)}</div>
              <div className="mt-3 grid gap-1 text-[13px] text-ink-700 sm:grid-cols-3"><div><span className="text-ink-400">剂数：</span>{r.doses_count ?? "—"} 剂</div><div className="sm:col-span-2"><span className="text-ink-400">煎法：</span>{r.decoction || "—"}</div><div className="sm:col-span-3"><span className="text-ink-400">服法：</span>{r.herbal_usage || "—"}</div></div></>) : (
              <div className="overflow-x-auto"><table className="tbl"><thead><tr><th>#</th><th>药品</th><th>规格</th><th>单次剂量</th><th>频次</th><th>途径</th><th>天数</th></tr></thead><tbody>{r.items.map((i: any) => <tr key={i.line_no}><td className="text-ink-400">{i.line_no}</td><td className="font-medium">{i.name}{!i.in_catalog && <span className="tag-warn ml-1.5">目录外</span>}</td><td>{i.spec || "—"}</td><td>{i.dose_text}</td><td>{i.frequency}</td><td>{i.route}</td><td>{i.days ?? "—"}</td></tr>)}</tbody></table></div>)}
            {r.notes && <div className="mt-3 rounded-lg bg-ink-50 p-2.5 text-sm text-ink-700"><span className="text-ink-400">备注：</span>{r.notes}</div>}
            {(r.dx_links || []).length > 0 && <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs text-ink-500">对应诊断：{r.dx_links.map((d: any) => <span key={d.id} className={d.module === "tcm" ? "tag-ok" : "tag-brand"}>{d.name}</span>)}</div>}
          </Card>
          <Card title="医师签署时的安全校验与确认" icon={<AlertTriangle size={15} className="text-amber-600" />}>
            {issues.length === 0 ? <div className="text-sm text-jade-700">规则引擎未发现问题。</div> : <ul className="space-y-2">{issues.map((i) => { const o = overrides.find((x) => x.key === i.key); return (
              <li key={i.key} className={`rounded-lg border p-2 text-[13px] ${SEV[i.severity].row}`}><div className="flex flex-wrap items-start gap-2"><span className={SEV[i.severity].cls}>{SEV[i.severity].label}</span><span className="min-w-0 flex-1">{i.message}</span></div>{o?.reason && <div className="mt-1 text-xs text-ink-600">医师确认/理由：<b>{o.reason}</b></div>}</li>); })}</ul>}
            <div className="mt-2 text-[11px] text-ink-400">规则库 {safety.rules_version}。规则提示仅为辅助，药师应独立核对。</div>
          </Card>
          {events.length > 0 && <Card title="状态与操作记录"><ol className="space-y-1.5 border-l border-ink-100 pl-3 text-[13px]">{events.map((e, i) => <li key={i}><span className="text-xs text-ink-400">{fmtTime(e.ts)}</span> · {e.actor} · <b>{e.action}</b>{e.to ? ` → ${e.to}` : ""}{e.note ? `：${e.note}` : ""}</li>)}</ol></Card>}
        </div>

        <aside className="space-y-4">
          <section className="card card-pad"><h2 className="h-sec mb-2">患者</h2><div className="text-sm"><span className="font-medium">{p.name}</span> <span className="text-ink-500">{p.gender || "—"} · {p.age != null ? `${p.age}岁` : "—"} · {p.mrn}</span></div>
            <div className={`mt-2 rounded-lg border px-2.5 py-2 text-[13px] ${p.allergies.length ? "border-danger-100 bg-danger-50/60" : "border-amber-200 bg-amber-50/70"}`}><div className="text-xs font-semibold">{p.allergies.length ? <span className="text-danger-700">过敏史</span> : <span className="text-amber-700">过敏史</span>}</div>{p.allergies.length ? <div className="mt-1 flex flex-wrap gap-1.5">{p.allergies.map((a: string) => <span key={a} className="tag-err">{a}</span>)}</div> : <div className="text-amber-800">未登记，请与医师核实</div>}</div></section>
          {r.review?.note && <Banner kind={r.status === "returned" ? "err" : "info"} title="药房备注">{r.review.note}</Banner>}
          {can("pharmacy.review") && ["signed", "pharmacy_review", "approved"].includes(r.status) && (
            <section className="card card-pad space-y-2"><h2 className="h-sec">药房处理</h2>
              {r.status === "signed" && <button className="btn-primary w-full" disabled={act.any} onClick={() => post("start", {}, "已接单，开始审核")}>{act.is("start") ? <Spinner size={14} /> : <ClipboardCheck size={14} />}接单并开始审核</button>}
              {r.status === "pharmacy_review" && <><button className="btn-success w-full" disabled={act.any} onClick={() => decide(true)}><Check size={14} />审核通过</button><button className="btn-danger-ghost w-full" disabled={act.any} onClick={() => decide(false)}><Undo2 size={14} />退回医师…</button></>}
              {r.status === "approved" && can("pharmacy.dispense") && <button className="btn-primary w-full" disabled={act.any} onClick={async () => { const c = await confirm({ title: "确认发药？", body: "发药后处方锁定，不能再修改或作废。", confirmText: "确认发药" }); if (c.ok) post("dispense", {}, "已发药"); }}>{act.is("dispense") ? <Spinner size={14} /> : <PackageCheck size={14} />}确认发药</button>}
            </section>)}
          {r.status === "dispensed" && <Banner kind="ok" title="已发药">{fmtTime(r.dispensed_at)}</Banner>}
          {r.signature_hash && <div className="break-all font-mono text-[10px] text-ink-300">签名摘要 {r.signature_hash}</div>}
        </aside>
      </div>
    </div>
  );
}
