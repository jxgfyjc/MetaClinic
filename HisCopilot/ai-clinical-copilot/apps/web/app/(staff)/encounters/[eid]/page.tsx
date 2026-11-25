"use client";
import { Suspense, use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { AlertTriangle, ArrowLeft, Ban, ClipboardList, FileText, HeartPulse, History, Pill, ShieldAlert, Sparkles, Stethoscope, UserCheck, UserPlus } from "lucide-react";
import { api, errMsg, fmtTime } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Banner, ErrorBox, Field, Modal, Skeleton, Spinner, StatusChip, useConfirm, useToast } from "@/components/ui";
import { useAction } from "@/lib/useAction";
import { Bundle, Step, WsCtx, isBusy, type Ws } from "@/components/workspace/ctx";
import { FactsPanel } from "@/components/workspace/FactsPanel";
import { MaterialsPanel } from "@/components/workspace/MaterialsPanel";
import { AiPanel } from "@/components/workspace/AiPanel";
import { PlanPanel } from "@/components/workspace/PlanPanel";
import { RxPanel } from "@/components/workspace/RxPanel";
import { RecordPanel } from "@/components/workspace/RecordPanel";
import { FollowupPanel } from "@/components/workspace/FollowupPanel";

const STEPS: { key: Step; label: string; icon: any }[] = [
  { key: "facts", label: "接诊资料", icon: Stethoscope }, { key: "ai", label: "AI 分析与诊断", icon: Sparkles }, { key: "plan", label: "治疗方案", icon: ClipboardList },
  { key: "rx", label: "处方", icon: Pill }, { key: "record", label: "病历", icon: FileText }, { key: "followup", label: "随访", icon: HeartPulse },
];

function Workspace({ eid }: { eid: string }) {
  const router = useRouter(); const sp = useSearchParams(); const { user, can } = useAuth(); const toast = useToast(); const confirm = useConfirm(); const act = useAction();
  const { data, error, mutate } = useSWR<Bundle>(`/encounters/${eid}`, { revalidateOnFocus: true, shouldRetryOnError: false, keepPreviousData: true,
    refreshInterval: (d) => (isBusy(d as Bundle | undefined) ? 2500 : 0) });
  const reload = useCallback(() => mutate(), [mutate]);
  const initial = (STEPS.find((s) => s.key === sp.get("step"))?.key || "facts") as Step;
  const [step, setStep] = useState<Step>(initial);
  const [visited, setVisited] = useState<Set<Step>>(new Set([initial]));
  const hpiAppend = useRef<((t: string) => void) | null>(null);
  const started = useRef<Promise<void> | null>(null);
  const go = useCallback((s: Step) => { setStep(s); setVisited((v) => new Set(v).add(s)); window.history.replaceState(null, "", `?step=${s}`); window.scrollTo({ top: 0 }); }, []);

  // doctors landing on a WAITING encounter they own are moved to IN_PROGRESS the first time they actually change something
  const ensureStarted = useCallback(async () => {
    if (!data || data.encounter.status !== "WAITING") return;
    if (!started.current) started.current = api.post(`/encounters/${eid}/start`).then(() => mutate()).then(() => undefined).finally(() => { started.current = null; });
    await started.current;
  }, [data, eid, mutate]);

  const writeOk = can("encounter.write_facts");
  const closed = !!data && ["COMPLETED", "CANCELLED"].includes(data.encounter.status);
  const readOnly = !writeOk || closed || (user?.role === "DOCTOR" && !!data?.encounter.doctor && data.encounter.doctor.id !== user.id);
  const mayAct = !!data && data.encounter.status !== "CANCELLED" && !(user?.role === "DOCTOR" && !!data.encounter.doctor && data.encounter.doctor.id !== user.id);
  const ws: Ws | null = useMemo(() => (data ? { eid, b: data, reload, readOnly, mayAct, ensureStarted, go, hpiAppend } : null), [data, eid, reload, readOnly, mayAct, ensureStarted, go]);
  const [cancelOpen, setCancelOpen] = useState(false); const [cancelReason, setCancelReason] = useState(""); const [alg, setAlg] = useState(false); const [newAlg, setNewAlg] = useState("");

  if (error && !data) return <div className="space-y-3"><Link href="/encounters" className="inline-flex items-center gap-1 text-xs text-ink-400 hover:text-brand-600"><ArrowLeft size={13} />今日接诊</Link><ErrorBox error={error} title="无法打开该就诊" onRetry={() => mutate()} /></div>;
  if (!data || !ws) return <div className="space-y-4"><Skeleton className="h-20 w-full" /><Skeleton className="h-10 w-2/3" /><Skeleton className="h-96 w-full" /></div>;

  const b = data; const e = b.encounter; const p = b.patient;
  const finals = b.diagnoses.filter((d) => d.status === "accepted" || d.status === "modified");
  const pendingDx = b.diagnoses.filter((d) => d.status === "pending").length;
  const jobsRunning = b.active_jobs.length > 0;
  const badge: Record<Step, React.ReactNode> = {
    facts: b.artifacts.some((a) => a.proc_status === "failed") ? <span className="h-2 w-2 rounded-full bg-danger-500" title="有资料处理失败" /> : !(e.chief_complaint || "").trim() ? <span className="h-2 w-2 rounded-full bg-amber-500" title="主诉未填写" /> : null,
    ai: ["queued", "running"].includes(b.analysis?.status) ? <Spinner size={12} className="text-brand-600" /> : pendingDx > 0 ? <span className="rounded-full bg-amber-100 px-1.5 text-[10px] font-semibold text-amber-700">{pendingDx}</span> : b.staleness.analysis_stale ? <span className="h-2 w-2 rounded-full bg-amber-500" title="资料已变化" /> : null,
    plan: b.plan?.status === "generating" ? <Spinner size={12} className="text-brand-600" /> : b.plan?.staleness?.stale ? <span className="h-2 w-2 rounded-full bg-amber-500" title="方案已过期" /> : b.plan?.status === "confirmed" ? <span className="h-2 w-2 rounded-full bg-jade-500" /> : null,
    rx: b.prescriptions.some((r) => ["draft", "returned"].includes(r.status)) ? <span className="rounded-full bg-amber-100 px-1.5 text-[10px] font-semibold text-amber-700">{b.prescriptions.filter((r) => ["draft", "returned"].includes(r.status)).length}</span> : b.prescriptions.length ? <span className="h-2 w-2 rounded-full bg-jade-500" /> : null,
    record: null, followup: null,
  };
  const noAllergy = p.allergies.length === 0;
  const claim = () => act.run("claim", async () => { await api.post(`/encounters/${eid}/claim`); await reload(); }, { ok: "已接诊", onStale: reload });
  const addAllergy = () => act.run("alg", async () => { await api.post(`/patients/${p.id}/allergies`, { allergen: newAlg.trim() }); setNewAlg(""); setAlg(false); await reload(); }, { ok: "已登记；已生成的 AI 分析/方案将提示“资料已变化”" });
  const cancelEnc = () => act.run("cancel", async () => { await api.post(`/encounters/${eid}/cancel`, { reason: cancelReason.trim() }); setCancelOpen(false); await reload(); }, { ok: "就诊已取消" });

  return (
    <WsCtx.Provider value={ws}>
      <div className="space-y-4">
        <div><Link href="/encounters" className="inline-flex items-center gap-1 text-xs text-ink-400 hover:text-brand-600"><ArrowLeft size={13} />今日接诊</Link></div>

        {/* ------------------------------------------------ patient safety header (always visible) */}
        <header className="card px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
            <div className="flex items-center gap-3"><span className="grid h-12 w-12 place-items-center rounded-2xl bg-brand-50 text-lg font-semibold text-brand-700">{p.name.slice(0, 1)}</span>
              <div><div className="flex flex-wrap items-center gap-2"><Link href={`/patients/${p.id}`} className="text-lg font-semibold text-ink-950 hover:text-brand-700">{p.name}</Link>{p.is_sample && <span className="tag-sim">示例数据</span>}{e.source === "patient_intake" && <span className="tag-brand">患者自填</span>}{e.visit_type !== "initial" && <span className="tag-neutral">{e.visit_type === "revisit" ? "复诊" : "随访触发"}</span>}</div>
                <div className="mt-0.5 text-[13px] text-ink-500">{p.gender || "性别未填"} · {p.age != null ? `${p.age} 岁` : "年龄未填"} · 门诊号 {p.mrn} · 就诊 <span className="font-mono text-xs">{e.encounter_no}</span></div></div></div>
            <div className={`min-w-0 flex-1 rounded-xl border px-3 py-2 ${noAllergy ? "border-amber-200 bg-amber-50/70" : "border-danger-100 bg-danger-50/60"}`}>
              <div className="flex items-center gap-1.5 text-xs font-semibold"><ShieldAlert size={14} className={noAllergy ? "text-amber-600" : "text-danger-600"} /><span className={noAllergy ? "text-amber-700" : "text-danger-700"}>过敏史</span>
                {!ws.readOnly && <button type="button" className="ml-auto text-[11px] font-normal text-brand-600 hover:underline" onClick={() => setAlg(true)}>＋登记</button>}</div>
              {noAllergy ? <div className="mt-0.5 text-[13px] text-amber-800"><b>尚未登记</b>——开药前请向患者核实（系统不会默认“无过敏”）</div> :
                <div className="mt-1 flex flex-wrap gap-1.5">{p.allergies.map((a: any) => <span key={a.id} className="tag-err" title={a.confirmed ? "医师已核实" : "患者自述，尚未核实"}>{a.allergen}{a.severity ? `（${a.severity}）` : ""}{!a.confirmed && <span className="opacity-60">·自述</span>}</span>)}</div>}
            </div>
            <div className="flex flex-col items-end gap-1.5"><div className="flex items-center gap-2"><StatusChip s={e.status} /><span className="text-xs text-ink-400">医师：{e.doctor?.name || <b className="text-amber-600">待接诊</b>}</span></div>
              <div className="flex flex-wrap justify-end gap-1.5">
                {b.can.claim && <button className="btn-primary btn-sm" disabled={act.any} onClick={claim}>{act.is("claim") ? <Spinner size={12} /> : <UserCheck size={13} />}接诊</button>}
                {b.previous_encounter && <Link href={`/encounters/${b.previous_encounter.id}`} className="btn-ghost btn-sm" title={b.previous_encounter.chief_complaint || ""}><History size={13} />上次就诊 {fmtTime(b.previous_encounter.created_at, false) && b.previous_encounter.created_at.slice(5, 10)}</Link>}
                {!closed && writeOk && <button className="btn-ghost btn-sm text-ink-400" onClick={() => setCancelOpen(true)}><Ban size={13} />取消就诊</button>}</div></div>
          </div>
        </header>

        {e.status === "CANCELLED" && <Banner kind="warn" title="该就诊已取消">{e.cancel_reason}</Banner>}
        {e.status === "COMPLETED" && <Banner kind="ok" title="本次就诊已完成">病历已定稿。资料、诊断、方案为只读；如需更正请在“病历”步骤使用“更正”。</Banner>}
        {jobsRunning && <div className="flex items-center gap-2 rounded-lg bg-brand-50/70 px-3 py-1.5 text-xs text-brand-800"><Spinner size={12} />后台任务进行中：{b.active_jobs.map((j) => `${({ asr: "语音识别", ocr: "OCR", tongue: "舌象分析", analysis: "AI 预分析", plan: "方案起草" } as any)[j.kind] || j.kind}${j.message ? `（${j.message}）` : ""}`).join("、")}，完成后自动刷新。</div>}

        {/* ------------------------------------------------ stepper */}
        <nav className="sticky top-0 z-30 -mx-4 flex items-center gap-2 border-b border-ink-100 bg-ink-50/95 px-4 backdrop-blur sm:-mx-6 sm:px-6" aria-label="诊疗步骤">
          <ol className="flex flex-1 gap-1 overflow-x-auto">{STEPS.map((s, i) => { const I = s.icon, on = step === s.key; return (
            <li key={s.key}><button type="button" onClick={() => go(s.key)} aria-current={on ? "step" : undefined}
              className={`relative flex shrink-0 items-center gap-2 px-3.5 py-3 text-sm font-medium transition ${on ? "text-brand-700" : "text-ink-500 hover:text-ink-800"}`}>
              <span className={`grid h-5 w-5 place-items-center rounded-full text-[10px] ${on ? "bg-brand-600 text-white" : "bg-ink-200 text-ink-600"}`}>{i + 1}</span><I size={15} />{s.label}{badge[s.key]}
              {on && <span className="absolute inset-x-2 bottom-0 h-0.5 rounded bg-brand-600" />}</button></li>); })}</ol>
          {step === "facts" && <button type="button" className="btn-primary btn-sm shrink-0" onClick={() => go("ai")}><Sparkles size={13} />下一步：AI 分析与诊断 →</button>}
        </nav>

        {/* ------------------------------------------------ panels (mounted on first visit, then kept so unsaved edits survive tab switches) */}
        <div className={step === "facts" ? "" : "hidden"}>{visited.has("facts") && <div className="grid gap-4 xl:grid-cols-2"><FactsPanel /><MaterialsPanel /></div>}
          {visited.has("facts") && <div className="mt-4 flex justify-end"><button className="btn-primary btn-lg" onClick={() => go("ai")}><Sparkles size={16} />下一步：AI 分析与诊断</button></div>}</div>
        <div className={step === "ai" ? "" : "hidden"}>{visited.has("ai") && <AiPanel />}</div>
        <div className={step === "plan" ? "" : "hidden"}>{visited.has("plan") && <PlanPanel />}</div>
        <div className={step === "rx" ? "" : "hidden"}>{visited.has("rx") && <RxPanel />}</div>
        <div className={step === "record" ? "" : "hidden"}>{visited.has("record") && <RecordPanel />}</div>
        <div className={step === "followup" ? "" : "hidden"}>{visited.has("followup") && <FollowupPanel />}</div>

        <p className="pb-6 pt-2 text-center text-[11px] text-ink-300">AI 输出仅为供医师参考的草稿。诊断、治疗方案、处方、病历均由医师最终确认；HIS 为模拟模式。</p>
      </div>

      <Modal open={alg} onClose={() => setAlg(false)} title="登记过敏史" width="max-w-sm" footer={<><button className="btn-secondary" onClick={() => setAlg(false)}>取消</button><button className="btn-primary" disabled={!newAlg.trim() || act.any} onClick={addAllergy}>登记</button></>}>
        <Field label="过敏原" hint="如：青霉素、头孢、磺胺、海鲜。医师登记视为已核实；将使已生成的 AI 分析 / 方案 / 处方校验过期。"><input className="input" autoFocus value={newAlg} onChange={(x) => setNewAlg(x.target.value)} onKeyDown={(x) => x.key === "Enter" && newAlg.trim() && addAllergy()} /></Field>
      </Modal>
      <Modal open={cancelOpen} onClose={() => setCancelOpen(false)} title="取消本次就诊" width="max-w-sm" footer={<><button className="btn-secondary" onClick={() => setCancelOpen(false)}>返回</button><button className="btn-danger" disabled={cancelReason.trim().length < 2 || act.any} onClick={cancelEnc}>确认取消</button></>}>
        <Field label="取消原因" required hint="已有已签署处方时需先作废处方。"><textarea className="input min-h-[72px]" autoFocus value={cancelReason} onChange={(x) => setCancelReason(x.target.value)} /></Field>
      </Modal>
    </WsCtx.Provider>
  );
}

export default function EncounterPage({ params }: { params: Promise<{ eid: string }> }) {
  const { eid } = use(params);
  return <Suspense fallback={<Skeleton className="h-96 w-full" />}><Workspace eid={eid} /></Suspense>;
}
