"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, ArrowRight, Ban, CheckCircle2, ChevronDown, ChevronRight, FilePlus2, FileText, Pill, Printer, RotateCcw, Save, Send, ShieldAlert, Undo2 } from "lucide-react";
import { api, blockersText, fmtTime, newKey } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Banner, Empty, Spinner, StatusChip, useConfirm, useToast, useUnsavedGuard } from "@/components/ui";
import { useAction } from "@/lib/useAction";
import { Card, sameJson } from "./bits";
import { useWs } from "./ctx";
import { HerbItemsEditor, IssueBadge, SEV, WesternItemsEditor, type Issue } from "./RxItems";

const TYPE_LABEL: Record<string, string> = { western: "西药", patent: "中成药", herbal: "中药饮片" };
const BLANK = (r: any) => !String(r.name || "").trim() && !String(r.dose_text || "").trim() && (r.dose_g == null || r.dose_g === "");
// Dirty-checking must compare CANONICAL values: rows added in the editor and rows returned by the server carry different extra keys
// (line_no, spec, form, unit…), and "" / undefined / null are all "empty". Comparing raw objects made a just-saved Rx look edited forever.
const ITEM_KEYS = ["name", "drug_id", "spec", "form", "dose_text", "frequency", "route", "days", "quantity", "quantity_unit", "dose_g", "special", "role", "instructions", "reason"];
const NUM_KEYS = new Set(["days", "quantity", "dose_g"]);
const canonItem = (i: any) => Object.fromEntries(ITEM_KEYS.map((k) => { const v = i[k]; return [k, NUM_KEYS.has(k) ? (v === "" || v == null ? null : Number(v)) : (v == null ? "" : String(v).trim())]; }));
const canonForm = (x: any) => ({ title: (x.title || "").trim(), notes: (x.notes || "").trim(), doses_count: x.doses_count ?? null, decoction: (x.decoction || "").trim(), herbal_usage: (x.herbal_usage || "").trim(), items: x.items.filter((i: any) => !BLANK(i)).map(canonItem) });

export function RxPanel() {
  const ws = useWs(); const { b, eid } = ws; const { user, can } = useAuth(); const act = useAction(); const toast = useToast();
  const [showVoided, setShowVoided] = useState(false);
  const sig = b.prescriptions.map((r) => `${r.id}:${r.status}:${r.row_version}`).join("|");
  const { data, error, mutate, isLoading } = useSWR(`/encounters/${eid}/prescriptions?include_voided=${showVoided}&_=${sig}`, { revalidateOnFocus: false, keepPreviousData: true });
  const list: any[] = data?.items || [];
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const seeded = useRef(false);
  useEffect(() => { if (!seeded.current && list.length) { seeded.current = true; setOpen(Object.fromEntries(list.filter((r) => ["draft", "returned"].includes(r.status)).slice(0, 3).map((r) => [r.id, true]))); } }, [list]);
  const refresh = async () => { await Promise.all([mutate(), ws.reload()]); };
  const completed = b.encounter.status === "COMPLETED";
  const writable = can("rx.write") && b.encounter.status !== "CANCELLED" && !(user?.role === "DOCTOR" && !!b.encounter.doctor && b.encounter.doctor.id !== user.id);   // per-card rules refine this
  const creating = writable && !completed;
  const plan = b.plan; const planUsable = plan && (plan.status === "draft" || plan.status === "confirmed");

  const create = (rx_type: string) => act.run(`new-${rx_type}`, async () => {
    await ws.ensureStarted();
    const r = await api.post(`/encounters/${eid}/prescriptions`, { rx_type, items: [], plan_id: planUsable && !plan.staleness?.stale ? plan.id : null }, { idempotencyKey: newKey() });
    setOpen((o) => ({ ...o, [r.prescription.id]: true })); await refresh();
  }, { ok: "已新建处方草稿" });

  const fromPlan = () => act.run("from-plan", async () => {
    const r = await api.post(`/encounters/${eid}/prescriptions/from-plan`, { plan_id: plan.id, kinds: ["western", "patent", "herbal"] }, { idempotencyKey: newKey() });
    toast(r.prescriptions.length ? "ok" : "info", r.prescriptions.length ? `已生成 ${r.prescriptions.length} 张处方草稿` : "方案内的药物都已生成过处方");
    setOpen((o) => ({ ...o, ...Object.fromEntries(r.prescriptions.map((x: any) => [x.id, true])) })); await refresh();
  }, { onStale: refresh });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="mr-auto text-sm text-ink-500">一次就诊可开<b>多张</b>处方（西药 / 中成药 / 中药饮片各自独立）。<b>处方由您逐张核对并签署</b>，系统不会自动签署或提交 HIS。</div>
        {creating && <>
          {planUsable && <button className="btn-secondary" disabled={act.any} onClick={fromPlan}>{act.is("from-plan") ? <Spinner size={14} /> : <FilePlus2 size={14} />}从治疗方案生成</button>}
          {(["western", "patent", "herbal"] as const).map((t) => <button key={t} className="btn-secondary" disabled={act.any} onClick={() => create(t)}>{act.is(`new-${t}`) ? <Spinner size={14} /> : <Pill size={14} />}新建{TYPE_LABEL[t]}处方</button>)}</>}
      </div>
      {plan?.staleness?.stale && planUsable && <Banner kind="warn" title="治疗方案已过期">从方案生成处方前，请先在“治疗方案”中重新生成或沿用方案。</Banner>}

      {isLoading && !data ? <Card><div className="flex justify-center py-6 text-ink-300"><Spinner size={20} /></div></Card> : error ? <Banner kind="err" title="处方加载失败" action={<button className="btn-secondary btn-sm" onClick={() => mutate()}>重试</button>}>{(error as any).message}</Banner> :
        list.length === 0 ? <Card><Empty icon={<Pill size={22} />} title="本次就诊还没有处方" hint={creating ? "可从治疗方案一键生成可编辑的草稿，或手工新建。不需要开药也可以直接去“病历”定稿。" : undefined} /></Card> : (
          <div className="space-y-3">{list.map((r) => <RxCard key={r.id} rx={r} open={!!open[r.id]} onToggle={() => setOpen((o) => ({ ...o, [r.id]: !o[r.id] }))} onChanged={refresh} writable={writable} />)}</div>)}
      {b.voided_prescriptions > 0 && <button className="text-xs text-ink-400 underline" onClick={() => setShowVoided(!showVoided)}>{showVoided ? "隐藏" : "显示"}已作废的处方（{b.voided_prescriptions}）</button>}
      <div className="flex justify-end"><button className="btn-primary btn-lg" onClick={() => ws.go("record")}>下一步：病历<ArrowRight size={16} /></button></div>
    </div>
  );
}

// ====================================================================================== one prescription
function RxCard({ rx, open, onToggle, onChanged, writable }: { rx: any; open: boolean; onToggle: () => void; onChanged: () => Promise<any>; writable: boolean }) {
  const ws = useWs(); const { user, can } = useAuth(); const act = useAction(); const confirm = useConfirm(); const toast = useToast();
  const mine = rx.doctor?.id === user?.id;
  // a finalized visit takes no new work — except a prescription the pharmacy returned (the doctor must be able to fix and re-sign it)
  const unlocked = ws.b.encounter.status !== "COMPLETED" || !!rx.review?.by;
  const editable = writable && unlocked && mine && (rx.status === "draft");
  const safety = rx.safety || {}; const issues: Issue[] = safety.issues || [];
  const toForm = (r: any) => ({ title: r.title || "", notes: r.notes || "", doses_count: r.doses_count ?? null, decoction: r.decoction || "", herbal_usage: r.herbal_usage || "", items: (r.items || []).map(({ line_no, ...x }: any) => x) });
  const server = useMemo(() => toForm(rx), [rx]);
  const [f, setF] = useState(server); const base = useRef(server);
  const norm = (x: any) => ({ ...x, items: x.items.filter((i: any) => !BLANK(i)) });          // what is sent to the server
  const dirty = open && !sameJson(canonForm(f), canonForm(base.current));
  useUnsavedGuard(dirty);
  useEffect(() => {
    if (sameJson(canonForm(server), canonForm(base.current))) { base.current = server; if (!dirty) setF(server); return; }
    if (!dirty) setF(server);                       // server changed and the doctor has nothing pending → follow the server
    base.current = server;
    /* eslint-disable-next-line */ }, [rx.row_version]);
  const set = (p: any) => setF((s: any) => ({ ...s, ...p }));
  const [acks, setAcks] = useState<Record<string, string>>(safety.acks || {});
  useEffect(() => { setAcks(safety.acks || {}); }, [safety.content_hash, rx.row_version]);   // eslint-disable-line
  const ev = useSWR(open ? `/prescriptions/${rx.id}?_=${rx.row_version}` : null, { revalidateOnFocus: false });

  const itemIssues = issues.filter((i) => i.item_index != null); const general = issues.filter((i) => i.item_index == null);
  const need = useMemo(() => {
    const out: { issue: Issue; need: "fix" | "ack" | "reason" }[] = [];
    for (const i of issues) {
      if (i.severity === "block") out.push({ issue: i, need: "fix" });
      else if (i.severity === "critical" && (acks[i.key] || "").trim().length < 5) out.push({ issue: i, need: "reason" });
      else if (i.severity === "warning" && !(acks[i.key] || "").trim()) out.push({ issue: i, need: "ack" });
    }
    return out;
  }, [issues, acks]);
  const isHerbal = rx.rx_type === "herbal";

  const save = () => act.run(`save-${rx.id}`, async () => {
    const payload: any = { title: f.title, notes: f.notes, items: norm(f).items, expected_version: rx.row_version };
    if (isHerbal) payload.header = { doses_count: f.doses_count, decoction: f.decoction, herbal_usage: f.herbal_usage };
    await api.patch(`/prescriptions/${rx.id}`, payload); await onChanged();
  }, { ok: "已保存，并已重新校验", onStale: onChanged });

  const saveAcks = async (version: number) => {
    const changed = Object.fromEntries(Object.entries(acks).filter(([k, v]) => v.trim() && (safety.acks || {})[k] !== v && issues.some((i) => i.key === k && (i.severity === "warning" || i.severity === "critical"))));
    if (!Object.keys(changed).length) return version;
    const r = await api.post(`/prescriptions/${rx.id}/ack`, { acks: changed }); return r.prescription.row_version as number;
  };
  const sign = async () => {
    if (dirty) return toast("warn", "请先保存修改");
    if (need.length) return toast("warn", "还有未处理的安全提示，请先逐条处理");
    const c = await confirm({ title: `签署处方 ${rx.rx_no}？`, body: <>签署后该处方进入药房审核，<b>不能再直接修改</b>（可在药房接单前“撤回签署”）。请确认您已核对药物、剂量与安全提示。{rx.ai_generated && <div className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-800">此处方由 AI 草拟，内容已由您审阅修改为准。</div>}</>, confirmText: "确认签署" });
    if (!c.ok) return;
    act.run(`sign-${rx.id}`, async () => { const ver = await saveAcks(rx.row_version); await api.post(`/prescriptions/${rx.id}/sign`, { expected_version: ver }); await onChanged(); },
      { ok: "已签署", onStale: onChanged, onError: (e) => { const t = blockersText(e); if (t.length) toast("err", t.join("；")); onChanged(); } });
  };
  const reasoned = async (title: string, body: string, label: string, path: string, ok: string, danger = false) => {
    const c = await confirm({ title, body, confirmText: "确认", danger, reason: { label, min: 2 } });
    if (c.ok) act.run(`${path}-${rx.id}`, async () => { await api.post(`/prescriptions/${rx.id}/${path}`, { reason: c.reason }); await onChanged(); }, { ok, onStale: onChanged });
  };
  const simple = (path: string, ok: string) => act.run(`${path}-${rx.id}`, async () => { await api.post(`/prescriptions/${rx.id}/${path}`); await onChanged(); }, { ok, onStale: onChanged });
  const recheck = () => simple("check", "已按最新的过敏/用药/资料重新校验");
  const mockHis = async () => {
    const c = await confirm({ title: "模拟提交 HIS？", body: <>系统<b>尚未接入真实 HIS</b>。这一步只会在本系统内生成一个以 MOCK- 开头的演练回执，<b>不会向任何外部系统发送医嘱</b>。</>, confirmText: "模拟提交" });
    if (c.ok) act.run(`his-${rx.id}`, async () => { const r = await api.post(`/prescriptions/${rx.id}/submit-his`); toast("info", r.notice); await onChanged(); }, { onStale: onChanged });
  };

  const canVoid = can("rx.void") && mine && ["draft", "signed", "pharmacy_review", "approved", "returned"].includes(rx.status);
  const header = (
    <button type="button" className="flex w-full flex-wrap items-center gap-2 px-4 py-3 text-left" onClick={onToggle} aria-expanded={open}>
      {open ? <ChevronDown size={16} className="text-ink-400" /> : <ChevronRight size={16} className="text-ink-400" />}
      <span className="font-mono text-xs text-ink-400">{rx.rx_no}</span><span className="font-semibold text-ink-900">{rx.title}</span><span className="tag-neutral">{TYPE_LABEL[rx.rx_type]}</span><StatusChip s={rx.status} />
      {rx.ai_generated && <span className="tag-brand">AI 草拟</span>}{rx.his?.mode === "mock" && rx.his.status === "mock_submitted" && <span className="tag-sim">模拟 HIS · {rx.his.ref}</span>}
      <span className="ml-auto flex items-center gap-2 text-xs text-ink-400">{rx.item_count} 项{rx.status === "draft" || rx.status === "returned" ? <IssueBadge summary={safety.summary} /> : null}</span>
    </button>);
  return (
    <section className={`card overflow-hidden ${rx.status === "returned" ? "border-danger-200" : rx.status === "voided" ? "opacity-70" : ""}`}>
      {header}
      {open && (
        <div className="space-y-4 border-t border-ink-100 p-4">
          {rx.status === "returned" && <Banner kind="err" title="药房退回了这张处方">{rx.review?.note || "（未写明原因）"}<div className="mt-1 text-xs opacity-80">请点击“重新编辑”修改后再次签署，或作废。{ws.b.encounter.status === "COMPLETED" && " 本次就诊的病历已定稿：处方改动后，请到“病历”用“更正”同步病历内容。"}</div></Banner>}
          {rx.status === "voided" && <Banner kind="info" title="已作废">{rx.void_reason}</Banner>}
          {rx.status === "dispensed" && <Banner kind="ok" title="已发药">{fmtTime(rx.dispensed_at)} · 已锁定，不可修改</Banner>}
          {!editable && rx.status === "draft" && !mine && <Banner kind="info">这张处方由 {rx.doctor?.name} 开具，您只能查看。</Banner>}

          <div className="grid gap-2 sm:grid-cols-[1fr_1fr]">
            <input className="input font-medium" disabled={!editable} value={f.title} onChange={(e) => set({ title: e.target.value })} aria-label="处方名称" placeholder="处方名称" />
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-500">{(rx.dx_links || []).map((d: any) => <span key={d.id} className={d.module === "tcm" ? "tag-ok" : "tag-brand"}>{d.name}</span>)}{rx.plan_id && <span className="tag-neutral">来自治疗方案</span>}</div>
          </div>

          {isHerbal ? <HerbItemsEditor readOnly={!editable} items={f.items} issues={itemIssues} onChange={(items) => set({ items })} /> : <WesternItemsEditor kind={rx.rx_type === "patent" ? "patent" : "western"} readOnly={!editable} items={f.items} issues={itemIssues} onChange={(items) => set({ items })} />}

          {isHerbal && (
            <div className="grid gap-2 sm:grid-cols-[7rem_1fr_1fr]">
              <div className="relative"><input className="input pr-7" type="number" min={1} max={60} disabled={!editable} value={f.doses_count ?? ""} onChange={(e) => set({ doses_count: e.target.value === "" ? null : Number(e.target.value) })} aria-label="剂数" placeholder="剂数" /><span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-ink-400">剂</span></div>
              <input className="input" disabled={!editable} value={f.decoction} onChange={(e) => set({ decoction: e.target.value })} aria-label="煎法" placeholder="煎法" /><input className="input" disabled={!editable} value={f.herbal_usage} onChange={(e) => set({ herbal_usage: e.target.value })} aria-label="服法" placeholder="服法" /></div>)}
          <textarea className="input min-h-[52px]" disabled={!editable} value={f.notes} onChange={(e) => set({ notes: e.target.value })} placeholder="备注 / 医嘱说明（可选）" aria-label="备注" />

          {/* ------------------------------------------------ safety */}
          {rx.status !== "voided" && (
            <div className={`rounded-xl border p-3 ${need.length ? "border-amber-200 bg-amber-50/40" : "border-jade-100 bg-jade-50/30"}`}>
              <div className="flex flex-wrap items-center gap-2"><ShieldAlert size={16} className={need.length ? "text-amber-600" : "text-jade-600"} /><span className="text-sm font-semibold text-ink-800">签署前安全校验</span><IssueBadge summary={safety.summary} />
                <span className="ml-auto text-[11px] text-ink-400">规则库 {safety.rules_version || "—"} · {safety.evaluated_at ? fmtTime(safety.evaluated_at) : ""}</span>
                {editable && <button type="button" className="btn-ghost btn-sm" disabled={act.any || dirty} title={dirty ? "请先保存" : "用最新的过敏/妊娠/用药资料重新校验"} onClick={recheck}><RotateCcw size={12} />重新校验</button>}</div>
              {dirty && <div className="mt-2 text-xs text-amber-700">校验结果对应已保存的版本，保存后会自动刷新。</div>}
              {issues.length === 0 ? <div className="mt-2 text-[13px] text-jade-700">规则引擎未发现问题。</div> : (
                <ul className="mt-2 space-y-2">{issues.map((i) => {
                  const a = acks[i.key] || "";
                  return (
                    <li key={i.key} className={`rounded-lg border p-2 ${SEV[i.severity].row}`}>
                      <div className="flex flex-wrap items-start gap-2 text-[13px]"><span className={SEV[i.severity].cls}>{SEV[i.severity].label}</span><span className="min-w-0 flex-1 text-ink-800">{i.message}</span></div>
                      {i.severity === "block" && <div className="mt-1 text-xs text-danger-600">必须修改处方后才能签署。</div>}
                      {i.severity === "warning" && editable && <label className="mt-1.5 flex items-center gap-2 text-xs text-ink-600"><input type="checkbox" className="h-4 w-4 accent-brand-600" checked={!!a.trim()} onChange={(e) => setAcks({ ...acks, [i.key]: e.target.checked ? "已知悉" : "" })} />我已知悉，仍按此处方</label>}
                      {i.severity === "critical" && editable && <div className="mt-1.5"><input className="input py-1.5 text-xs" placeholder="严重提示：请写明仍然开具的临床理由（至少 5 个字，将记入审计）" value={a} onChange={(e) => setAcks({ ...acks, [i.key]: e.target.value })} aria-label="仍开具的理由" />{a.trim() && a.trim().length < 5 && <div className="mt-0.5 text-[11px] text-danger-600">理由至少 5 个字</div>}</div>}
                      {(i.severity === "warning" || i.severity === "critical") && !editable && a && <div className="mt-1 text-xs text-ink-500">医师确认：{a}</div>}
                    </li>);
                })}</ul>)}
              <div className="mt-2 text-[11px] leading-relaxed text-ink-400">规则校验仅为辅助，<b>未命中不代表安全</b>。严重/必须处理的提示不能被默认忽略。</div>
            </div>)}

          {/* ------------------------------------------------ actions */}
          <div className="flex flex-wrap items-center gap-2 border-t border-ink-50 pt-3">
            {editable && <>
              <button className="btn-secondary" disabled={act.any || !dirty} onClick={save}>{act.is(`save-${rx.id}`) ? <Spinner size={14} /> : <Save size={14} />}保存修改</button>
              <button className="btn-secondary" disabled={act.any || !dirty} onClick={() => setF(base.current)}><Undo2 size={14} />放弃修改</button>
              {can("rx.sign") && <button className="btn-success" disabled={act.any || dirty || need.length > 0 || f.items.filter((i: any) => !BLANK(i)).length === 0} title={dirty ? "请先保存" : need.length ? `还有 ${need.length} 项安全提示待处理` : ""} onClick={sign}>{act.is(`sign-${rx.id}`) ? <Spinner size={14} /> : <CheckCircle2 size={14} />}签署处方</button>}
              {dirty && <span className="text-xs text-amber-700">有未保存的修改，请先保存后再签署</span>}
              {need.length > 0 && !dirty && <span className="text-xs text-amber-700">还有 {need.length} 项安全提示待处理</span>}
              {!dirty && need.length === 0 && f.items.filter((i: any) => !BLANK(i)).length === 0 && <span className="text-xs text-amber-700">处方里还没有药品</span>}</>}
            {writable && unlocked && mine && rx.status === "returned" && <button className="btn-primary" disabled={act.any} onClick={() => simple("reopen", "已转为草稿，可修改后重新签署")}><Undo2 size={14} />重新编辑</button>}
            {writable && mine && rx.status === "signed" && can("rx.sign") && <button className="btn-secondary" disabled={act.any} onClick={() => reasoned("撤回签署？", "撤回后处方回到草稿，可修改后重新签署。仅在药房尚未接单时可撤回。", "撤回原因", "unsign", "已撤回签署")}><Undo2 size={14} />撤回签署</button>}
            {writable && mine && ["signed", "pharmacy_review", "approved", "dispensed"].includes(rx.status) && can("rx.sign") && rx.his?.status !== "mock_submitted" && <button className="btn-secondary" disabled={act.any} onClick={mockHis}>{act.is(`his-${rx.id}`) ? <Spinner size={14} /> : <Send size={14} />}模拟提交 HIS（演练）</button>}
            <a className="btn-secondary" href={`/api/v1/prescriptions/${rx.id}/pdf`} target="_blank" rel="noreferrer"><Printer size={14} />打印 / PDF</a>
            {canVoid && <button className="btn-danger-ghost ml-auto" disabled={act.any} onClick={() => reasoned("作废这张处方？", "作废不可恢复，请写明原因。药房已发药的处方不能作废。", "作废原因", "void", "已作废", true)}><Ban size={14} />作废</button>}
          </div>

          {(ev.data?.events || []).length > 0 && (
            <details className="text-xs text-ink-500"><summary className="cursor-pointer font-medium text-ink-600">状态与操作记录（{ev.data.events.length}）</summary>
              <ol className="mt-1.5 space-y-1 border-l border-ink-100 pl-3">{ev.data.events.map((e: any, i: number) => <li key={i}><span className="text-ink-400">{fmtTime(e.ts)}</span> · {e.actor} · <b>{e.action}</b>{e.to ? ` → ${e.to}` : ""}{e.note ? `：${e.note}` : ""}</li>)}</ol></details>)}
          {rx.signature_hash && <div className="font-mono text-[10px] text-ink-300">签名摘要 {rx.signature_hash.slice(0, 24)}…</div>}
        </div>)}
    </section>
  );
}
