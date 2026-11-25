"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { AlertTriangle, ArrowRight, Check, CheckCircle2, ClipboardList, FilePlus2, Pencil, Plus, RefreshCw, Save, ShieldAlert, Sparkles, Trash2, Undo2, X, XCircle } from "lucide-react";
import { api, ApiError, blockersText, errMsg, fmtTime, newKey } from "@/lib/api";
import { Banner, Empty, Modal, Spinner, StatusChip, useConfirm, useToast, useUnsavedGuard } from "@/components/ui";
import { useAction } from "@/lib/useAction";
import { Bullets, Card, Collapse, Elapsed, LinesEditor, Working, sameJson } from "./bits";
import { useWs } from "./ctx";
import { HerbItemsEditor, IssueBadge, SEV, WesternItemsEditor, type Issue } from "./RxItems";
import { StyleColumns, StylePicker } from "./StylePanel";

const SECTIONS: [string, string, string][] = [["medication", "西药 / 中成药", "通用名 + 剂量 + 频次 + 疗程"], ["herbal", "中药饮片方", "君臣佐使，10–14 味"], ["therapy", "针灸 / 推拿 / 理疗等", "最多 3 项，穴位名会被核对"]];
const PLAN_ERR: Record<string, string> = { no_final_diagnoses: "没有最终诊断", model_oom: "显存繁忙，请稍后重试", bad_output: "模型输出格式异常（已自动重试仍失败）", model_http_error: "模型服务返回错误", cancelled: "任务已取消" };
const SRC: Record<string, [string, string]> = { hospital: ["院内项目", "tag-ok"], knowledge: ["知识库", "tag-brand"], ai: ["AI 建议（未核实）", "tag-warn"], doctor: ["医师添加", "tag-neutral"] };

export function PlanPanel() {
  const ws = useWs(); const { b, eid, readOnly } = ws; const plan = b.plan; const act = useAction(); const confirm = useConfirm(); const toast = useToast();
  const finals = b.diagnoses.filter((d) => d.status === "accepted" || d.status === "modified");
  const [secs, setSecs] = useState<string[]>(["medication", "herbal", "therapy"]);
  const genKey = useRef(newKey());

  const generate = async (reason = "") => {
    if (!secs.length) return toast("warn", "请至少选择一个需要起草的部分");
    await act.run("gen", async () => {
      await ws.ensureStarted();
      const r = await api.post(`/encounters/${eid}/plan/generate`, { sections: secs, reason }, { idempotencyKey: genKey.current });
      genKey.current = newKey();
      if (r.reused) toast("info", r.reason || "沿用现有方案");
      await ws.reload();
    }, { onStale: ws.reload });
  };
  const manual = () => act.run("manual", async () => { await ws.ensureStarted(); await api.post(`/encounters/${eid}/plan/manual`, { sections: secs }); await ws.reload(); }, { ok: "已新建空白方案，请手工填写" });

  if (finals.length === 0)
    return <Card><Empty icon={<ClipboardList size={22} />} title="先确认诊断，再制定治疗方案" hint="治疗方案是基于全部最终诊断统一制定的一份方案，而不是每个诊断各写一份。请先在“AI 分析与诊断”中接受/修改/添加诊断。" action={<button className="btn-primary mt-2" onClick={() => ws.go("ai")}>去确认诊断<ArrowRight size={15} /></button>} /></Card>;

  const picker = (<div className="space-y-3">
    <div className="grid gap-2 sm:grid-cols-3">{SECTIONS.map(([k, t, h]) => (
      <label key={k} className={`flex cursor-pointer items-start gap-2 rounded-xl border p-3 text-sm transition ${secs.includes(k) ? "border-brand-300 bg-brand-50/60" : "border-ink-100 bg-white hover:bg-ink-50"}`}>
        <input type="checkbox" className="mt-0.5 h-4 w-4 accent-brand-600" checked={secs.includes(k)} onChange={(e) => setSecs(e.target.checked ? [...secs, k] : secs.filter((x) => x !== k))} />
        <span><span className="font-medium text-ink-800">{t}</span><span className="block text-xs text-ink-400">{h}</span></span></label>))}</div>
    <StylePicker eid={eid} disabled={readOnly} />
  </div>);

  if (!plan)
    return (
      <div className="space-y-4">
        <DxChips finals={finals} />
        <Card title="起草整体治疗方案" icon={<Sparkles size={16} className="text-brand-600" />}>
          <p className="mb-3 text-sm text-ink-600">AI 会综合 <b>{finals.length} 个最终诊断</b>、患者过敏/妊娠/用药等安全信息，起草一份<b>草稿</b>。耗时约 1–2 分钟，在后台运行。草稿不会自动生效，更不会自动开处方。</p>
          {picker}
          <div className="mt-4 flex flex-wrap gap-2">
            {!readOnly && <button className="btn-primary" disabled={act.any || !secs.length} onClick={() => generate()}>{act.is("gen") ? <Spinner size={15} /> : <Sparkles size={15} />}AI 起草治疗方案</button>}
            {!readOnly && <button className="btn-secondary" disabled={act.any} onClick={manual}>{act.is("manual") ? <Spinner size={14} /> : <Pencil size={14} />}不用 AI，手工新建</button>}</div>
        </Card>
      </div>);

  if (plan.status === "generating")
    return (
      <div className="space-y-4"><DxChips finals={finals} snapshot={plan.dx_snapshot} />
        <Card tone="warn"><Working label="AI 正在起草整体治疗方案…" sub={<>已用时 <Elapsed since={plan.created_at} />　· 约 1–2 分钟，后台运行，可离开本页，完成后会保存为草稿</>} />
          {b.active_jobs.find((j) => j.kind === "plan")?.message && <div className="mt-2 text-xs text-ink-500">{b.active_jobs.find((j) => j.kind === "plan")!.message}</div>}
          {!readOnly && plan.job_id && <button className="btn-ghost btn-sm mt-3" disabled={act.any} onClick={() => act.run("cancel", async () => { await api.post(`/jobs/${plan.job_id}/cancel`); await ws.reload(); }, { ok: "已请求取消" })}>取消生成</button>}</Card></div>);

  if (plan.status === "failed")
    return (
      <div className="space-y-4"><DxChips finals={finals} />
        <Card tone="err"><div className="flex items-start gap-3"><XCircle className="mt-0.5 shrink-0 text-danger-600" size={20} /><div className="min-w-0 flex-1">
          <div className="font-medium text-danger-700">治疗方案生成失败</div><div className="mt-1 text-sm text-ink-700">{PLAN_ERR[plan.error_code] || "生成未能完成。"}</div>
          {plan.error_detail && <div className="mt-1 break-words rounded bg-danger-50 p-2 font-mono text-[11px] text-danger-700">{plan.error_code && `[${plan.error_code}] `}{plan.error_detail}</div>}
          <div className="mt-3">{picker}</div></div></div>
          {!readOnly && <div className="mt-4 flex flex-wrap gap-2"><button className="btn-primary" disabled={act.any} onClick={() => generate("重试")}>{act.is("gen") ? <Spinner size={14} /> : <RefreshCw size={14} />}重试生成</button>
            <button className="btn-secondary" disabled={act.any} onClick={manual}>改为手工新建</button></div>}</Card></div>);

  return <PlanEditor key={plan.id} plan={plan} finals={finals} picker={picker} secs={secs} generate={generate} manual={manual} genBusy={act.is("gen")} />;
}

function DxChips({ finals, snapshot }: { finals: any[]; snapshot?: any[] }) {
  const now = new Set(finals.map((d) => d.id));
  const list = snapshot?.length ? snapshot : finals.map((d) => ({ id: d.id, module: d.module, name: d.name, primary: d.is_primary }));
  const gone = list.filter((d: any) => !now.has(d.id));
  const added = finals.filter((d) => !list.some((x: any) => x.id === d.id));
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-ink-100 bg-white px-3.5 py-2.5 text-[13px]">
      <span className="font-medium text-ink-600">{snapshot ? "本方案基于的诊断：" : "将基于以下最终诊断："}</span>
      {list.map((d: any) => <span key={d.id} className={`${d.module === "tcm" ? "tag-ok" : "tag-brand"} ${now.has(d.id) ? "" : "line-through opacity-60"}`} title={now.has(d.id) ? "" : "该诊断已被移除/退回"}>{d.name}{d.primary ? " ★" : ""}</span>)}
      {added.map((d) => <span key={d.id} className="tag-warn" title="方案生成之后新增的诊断">＋{d.name}（新增）</span>)}
      {gone.length > 0 && <span className="text-xs text-amber-700">有 {gone.length} 个诊断已被移除</span>}
    </div>);
}

// ====================================================================================== editor
function PlanEditor({ plan, finals, picker, secs, generate, manual, genBusy }: { plan: any; finals: any[]; picker: React.ReactNode; secs: string[]; generate: (r?: string) => Promise<void>; manual: () => void; genBusy: boolean }) {
  const ws = useWs(); const { b, eid, readOnly } = ws; const act = useAction(); const confirm = useConfirm(); const toast = useToast();
  const [c, setC] = useState<any>(plan.content); const base = useRef<any>(plan.content);
  const dirty = !sameJson(c, base.current);
  useUnsavedGuard(dirty);
  useEffect(() => { if (!sameJson(plan.content, base.current)) { if (!dirty) { setC(plan.content); } base.current = plan.content; } /* eslint-disable-next-line */ }, [plan.row_version]);
  const st = plan.staleness || {}; const stale = !!st.stale;
  const locked = readOnly || b.encounter.status === "COMPLETED";
  const editable = !locked;
  const [rebaseOpen, setRebaseOpen] = useState(false); const [regenOpen, setRegenOpen] = useState(false);
  const preview = useSWR(`/plans/${plan.id}/safety-preview?v=${plan.row_version}&i=${b.staleness.inputs_version}`, { revalidateOnFocus: false });
  const groups: any[] = preview.data?.groups || [];
  const gIssues = (ref: string): Issue[] => groups.find((g) => g.ref === ref)?.issues || [];
  const pendingTh = plan.therapy_items.filter((t: any) => t.status === "pending").length;
  const set = (k: string, v: any) => setC((s: any) => ({ ...s, [k]: v }));
  const med = c.medication || { western: [], patent: [] };

  const save = () => act.run("save", async () => {
    const r = await api.patch(`/plans/${plan.id}`, { content: c, expected_version: plan.row_version });
    base.current = r.plan.content; await ws.reload();
  }, { ok: "已保存", onStale: ws.reload });

  const confirmPlan = async () => {
    if (dirty) { toast("warn", "请先保存修改再确认"); return; }
    const r = await confirm({ title: "确认这份治疗方案？", body: <>确认表示您已审阅并认可本方案的全部内容。确认后仍可修改，但修改会使其退回“草稿”，需重新确认。<br />确认<b>不会</b>自动开处方，也不会提交 HIS。</>, confirmText: "确认方案" });
    if (!r.ok) return;
    act.run("confirm", async () => { await api.post(`/plans/${plan.id}/confirm`, { expected_version: plan.row_version }); await ws.reload(); }, {
      ok: "治疗方案已确认", onStale: ws.reload,
      onError: (e) => { const t = blockersText(e); if (t.length) toast("err", t.join("；")); }
    });
  };

  const makeRx = async (ack = "") => {
    await act.run("rx", async () => {
      try {
        const r = await api.post(`/encounters/${eid}/prescriptions/from-plan`, { plan_id: plan.id, kinds: ["western", "patent", "herbal"], acknowledge_stale: ack }, { idempotencyKey: newKey() });
        const n = r.prescriptions.length;
        toast(n ? "ok" : "info", n ? `已生成 ${n} 张处方草稿，请逐张核对、签署` : "方案内的药物都已生成过处方，没有新的草稿");
        await ws.reload(); if (n) ws.go("rx");
      } catch (e) {
        if (e instanceof ApiError && e.code === "plan_stale") {
          const c2 = await confirm({ title: "方案已过期，仍要据此开处方？", body: "诊断或临床资料在方案生成后有变化。建议先“重新生成”或“沿用方案”。如确需继续，请写明理由（会记入审计）。", confirmText: "仍然生成处方草稿", danger: true, reason: { label: "继续的理由", min: 5 } });
          if (c2.ok) await makeRx(c2.reason);
          return;
        }
        throw e;
      }
    }, { onStale: ws.reload });
  };

  const hasDrugs = (med.western?.length || 0) + (med.patent?.length || 0) + (c.herbal?.length || 0) > 0;
  const confirmed = plan.status === "confirmed";
  const [subTab, setSubTab] = useState<"herbal" | "med" | "therapy" | "followup" | "all">((c.herbal || []).length > 0 ? "herbal" : "med");

  const confirmAndMakeRx = async () => {
    if (dirty) { toast("warn", "请先保存修改"); return; }
    const r = await confirm({ title: "确认治疗方案并生成处方？", body: <>确认后本方案即生效，并自动为您起草对应的西药/中成药/中药饮片处方草稿，供下一步核对签署。</>, confirmText: "确认并生成处方" });
    if (!r.ok) return;
    await act.run("confirm-rx", async () => {
      await api.post(`/plans/${plan.id}/confirm`, { expected_version: plan.row_version });
      const rxRes = await api.post(`/encounters/${eid}/prescriptions/from-plan`, { plan_id: plan.id, kinds: ["western", "patent", "herbal"], acknowledge_stale: "" }, { idempotencyKey: newKey() });
      const n = rxRes.prescriptions.length;
      toast(n ? "ok" : "info", n ? `方案已确认，已生成 ${n} 张处方草稿` : "方案已确认");
      await ws.reload();
      if (n) ws.go("rx");
    }, { onStale: ws.reload, onError: (e) => { const t = blockersText(e); if (t.length) toast("err", t.join("；")); } });
  };

  return (
    <div className="space-y-4">
      <DxChips finals={finals} snapshot={plan.dx_snapshot} />

      {stale && (
        <Banner kind="warn" title="本方案已过期：诊断或临床资料在方案生成之后发生了变化"
          action={editable && <div className="flex shrink-0 flex-col gap-1.5 sm:flex-row"><button className="btn-primary btn-sm" disabled={act.any} onClick={() => setRegenOpen(true)}><RefreshCw size={12} />重新生成</button><button className="btn-secondary btn-sm" disabled={act.any || dirty} title={dirty ? "请先保存修改" : ""} onClick={() => setRebaseOpen(true)}>仍沿用本方案…</button></div>}>
          {st.dx_changed && <div>· 最终诊断集合已变化（见上方诊断标签）</div>}{st.inputs_changed && <div>· 接诊资料已变化（病史 / 检验 / 过敏 / 语音 / 舌象等）</div>}
          <div className="mt-0.5 text-xs opacity-80">过期的方案不能直接确认。重新生成会保留旧方案为历史；“沿用”会保留您的全部修改，但需写明理由并重新确认。</div></Banner>)}

      <Card title={<span className="flex items-center gap-2">整体治疗方案 <span className="text-xs font-normal text-ink-400">v{plan.version_no}</span><StatusChip s={plan.status} />{plan.ai_used ? <span className="tag-brand">AI 草拟</span> : <span className="tag-neutral">医师手工</span>}</span>}
        icon={<ClipboardList size={16} className="text-brand-600" />}
        right={<span className="text-xs text-ink-400">{confirmed ? `已确认 ${fmtTime(plan.confirmed_at)}` : `创建 ${fmtTime(plan.created_at)}`}{plan.models?.plan ? ` · ${plan.models.plan}` : ""}</span>}>
        <div className="space-y-3">
          <Sec title="方案概述（各部分如何协同）"><textarea className="input min-h-[48px]" rows={2} disabled={!editable} value={c.summary || ""} onChange={(e) => set("summary", e.target.value)} placeholder="一句话说明西药、中药、理疗各自的作用与分工" /></Sec>
          {(c.coordination_notes?.length > 0 || editable) && (
            <Collapse title={`多诊断协调要点${(c.coordination_notes?.length || 0) > 0 ? `（${c.coordination_notes.length} 条）` : ""}`}>
              <LinesEditor value={c.coordination_notes || []} onChange={(v) => set("coordination_notes", v)} disabled={!editable} rows={2} />
            </Collapse>
          )}
        </div>
      </Card>

      {/* ----------------------------------------------------- 经验画像参考：四栏对照（没有选用画像时完全不渲染） */}
      <StyleColumns plan={plan} editable={editable} />

      {/* ----------------------------------------------------- safety preview */}
      <SafetyPreview loading={preview.isLoading} error={preview.error} summary={preview.data?.summary} groups={groups} dirty={dirty} onRetry={() => preview.mutate()} />

      {/* ----------------------------------------------------- segmented detail tabs */}
      <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-ink-100 bg-white p-1.5">
        {([
          ["herbal", `中药饮片方 (${(c.herbal || []).length})`, 0],
          ["med", `西药 / 中成药 (${(med.western?.length || 0) + (med.patent?.length || 0)})`, 0],
          ["therapy", `针灸 / 推拿 / 理疗 (${(plan.therapy_items || []).length})`, pendingTh],
          ["followup", "调护与随访", 0],
          ["all", "全部展开", 0],
        ] as const).map(([k, lbl, badgeN]) => (
          <button key={k} type="button" onClick={() => setSubTab(k)}
            className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${subTab === k ? "bg-brand-600 text-white" : "text-ink-600 hover:bg-ink-100"}`}>
            <span>{lbl}</span>
            {badgeN > 0 && <span className={`rounded-full px-1.5 text-[10px] font-semibold ${subTab === k ? "bg-white/20 text-white" : "bg-amber-100 text-amber-800"}`}>{badgeN} 待审</span>}
          </button>
        ))}
      </div>

      {/* ----------------------------------------------------- herbal */}
      {(subTab === "herbal" || subTab === "all") && (
        <Card title="中药饮片方" right={editable && <button type="button" className="btn-secondary btn-sm" onClick={() => set("herbal", [...(c.herbal || []), { title: "自拟方", formula_type: "ai", source_note: "", principle: "", herbs: [], doses_count: 7, decoction: "", usage: "", rationale: "", cautions: [] }])}><Plus size={13} />添加一个方</button>}>
          {(c.herbal || []).length === 0 && <div className="text-sm text-ink-400">本方案没有中药饮片方。</div>}
          <div className="space-y-4">{(c.herbal || []).map((h: any, i: number) => {
            const sh = (p: any) => set("herbal", c.herbal.map((x: any, j: number) => (j === i ? { ...x, ...p } : x)));
            return (
              <div key={i} className="rounded-xl border border-ink-100 p-3.5">
                <div className="grid gap-2 sm:grid-cols-[1fr_8rem_1fr_auto]">
                  <input className="input font-medium" disabled={!editable} value={h.title || ""} onChange={(e) => sh({ title: e.target.value })} aria-label="方名" placeholder="方名" />
                  <select className="input" disabled={!editable} value={h.formula_type || "ai"} onChange={(e) => sh({ formula_type: e.target.value })} aria-label="方剂来源"><option value="classic">经方/古方</option><option value="hospital">院内协定方</option><option value="ai">自拟/AI 拟</option></select>
                  <input className="input" disabled={!editable} value={h.source_note || ""} onChange={(e) => sh({ source_note: e.target.value })} aria-label="化裁自" placeholder="化裁自（如：生脉散加减）" />
                  {editable && <button type="button" className="btn-ghost btn-sm text-ink-400 hover:text-danger-600" aria-label="删除此方" onClick={async () => { const r = await confirm({ title: `删除「${h.title || "此方"}」？`, confirmText: "删除", danger: true }); if (r.ok) set("herbal", c.herbal.filter((_: any, j: number) => j !== i)); }}><Trash2 size={14} /></button>}
                </div>
                <input className="input mt-2" disabled={!editable} value={h.principle || ""} onChange={(e) => sh({ principle: e.target.value })} placeholder="治法，如：益气养阴、清热生津" aria-label="治法" />
                <div className="mt-3"><HerbItemsEditor readOnly={!editable} items={h.herbs || []} issues={gIssues(`herbal:${i}`)} onChange={(v) => sh({ herbs: v })} /></div>
                <div className="mt-3 grid gap-2 sm:grid-cols-[6rem_1fr_1fr]">
                  <div className="relative"><input className="input pr-7" type="number" min={1} max={60} disabled={!editable} value={h.doses_count ?? ""} onChange={(e) => sh({ doses_count: e.target.value === "" ? null : Number(e.target.value) })} aria-label="剂数" /><span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-xs text-ink-400">剂</span></div>
                  <input className="input" disabled={!editable} value={h.decoction || ""} onChange={(e) => sh({ decoction: e.target.value })} placeholder="煎法，如：水煎 400ml，先煎…" aria-label="煎法" />
                  <input className="input" disabled={!editable} value={h.usage || ""} onChange={(e) => sh({ usage: e.target.value })} placeholder="服法，如：每日 1 剂，早晚温服" aria-label="服法" />
                </div>
                {(h.rationale || editable) && <textarea className="input mt-2 min-h-[48px]" disabled={!editable} value={h.rationale || ""} onChange={(e) => sh({ rationale: e.target.value })} placeholder="组方思路（可选）" aria-label="组方思路" />}
                {(h.cautions || []).length > 0 && <div className="mt-2 text-xs text-amber-700">注意：{h.cautions.join("；")}</div>}
              </div>);
          })}</div>
        </Card>)}

      {/* ----------------------------------------------------- medication */}
      {(subTab === "med" || subTab === "all") && (
        <Card title="西药 / 中成药" right={<span className="hint">药名可从药品目录选择；目录外药物会被标记</span>}>
          <div className="space-y-4">
            <div><div className="mb-1.5 text-[13px] font-semibold text-ink-700">西药</div><WesternItemsEditor kind="western" readOnly={!editable} items={med.western || []} issues={gIssues("western")} onChange={(v) => set("medication", { ...med, western: v })} /></div>
            <div><div className="mb-1.5 text-[13px] font-semibold text-ink-700">中成药</div><WesternItemsEditor kind="patent" readOnly={!editable} items={med.patent || []} issues={gIssues("patent")} onChange={(v) => set("medication", { ...med, patent: v })} /></div>
          </div>
        </Card>)}

      {/* ----------------------------------------------------- therapy */}
      {(subTab === "therapy" || subTab === "all") && <TherapyCard plan={plan} editable={editable} />}

      {/* ----------------------------------------------------- the rest */}
      {(subTab === "followup" || subTab === "all") && (
        <Card title="调护、监测与随访建议">
          <div className="grid gap-4 lg:grid-cols-2">
            <Sec title="生活调护" hint="每行一条"><LinesEditor value={c.lifestyle || []} onChange={(v) => set("lifestyle", v)} disabled={!editable} rows={3} /></Sec>
            <Sec title="监测 / 复查" hint="每行一条，含时间"><LinesEditor value={c.monitoring || []} onChange={(v) => set("monitoring", v)} disabled={!editable} rows={3} /></Sec>
            <Sec title="复诊 / 随访建议"><textarea className="input min-h-[64px]" disabled={!editable} value={c.followup_advice || ""} onChange={(e) => set("followup_advice", e.target.value)} /></Sec>
            <Sec title="用药安全提示（AI 给出，可修改）"><LinesEditor value={c.safety_notes || []} onChange={(v) => set("safety_notes", v)} disabled={!editable} rows={3} /></Sec>
          </div>
          {(c.missing_info || []).length > 0 && <div className="mt-4 rounded-xl border border-amber-100 bg-amber-50/60 p-3"><div className="mb-1 text-[13px] font-semibold text-amber-800">AI 标注：需要医师确认的缺失信息</div><Bullets items={c.missing_info} tone="warn" /></div>}
        </Card>)}

      {/* ----------------------------------------------------- action bar */}
      {editable && (
        <div className="sticky bottom-0 z-20 -mx-4 border-t border-ink-100 bg-white/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
          <div className="mx-auto flex max-w-[1380px] flex-wrap items-center gap-2">
            <div className="mr-auto text-xs">{dirty ? <span className="text-amber-700">有未保存的修改</span> : pendingTh > 0 ? <button type="button" className="font-medium text-amber-700 underline hover:text-amber-900" onClick={() => setSubTab("therapy")}>去审核 {pendingTh} 项理疗 →</button> : stale ? <span className="text-amber-700">方案已过期</span> : confirmed ? <span className="flex items-center gap-1 text-jade-600"><CheckCircle2 size={13} />方案已确认</span> : <span className="text-ink-400">审阅无误后可确认方案并生成处方</span>}</div>
            <button className="btn-secondary" disabled={act.any} onClick={() => setRegenOpen(true)}><RefreshCw size={14} />重新生成</button>
            <button className="btn-secondary" disabled={act.any || !dirty} onClick={() => { setC(base.current); }}><Undo2 size={14} />放弃修改</button>
            <button className="btn-secondary" disabled={act.any || !dirty} onClick={save}>{act.is("save") ? <Spinner size={14} /> : <Save size={14} />}保存修改</button>
            {!confirmed && <button className="btn-success" disabled={act.any || dirty || stale || pendingTh > 0} title={dirty ? "请先保存" : stale ? "方案已过期" : pendingTh ? "请先审核治疗项目" : ""} onClick={confirmPlan}>{act.is("confirm") ? <Spinner size={14} /> : <Check size={14} />}确认方案</button>}
            {!confirmed && !dirty && !stale && pendingTh === 0 && hasDrugs && (
              <button className="btn-primary" disabled={act.any} onClick={confirmAndMakeRx}>{act.is("confirm-rx") ? <Spinner size={14} /> : <FilePlus2 size={14} />}确认方案并生成处方<ArrowRight size={14} /></button>
            )}
            {(confirmed || pendingTh > 0 || !hasDrugs) && (
              <button className="btn-primary" disabled={act.any || dirty || !hasDrugs} title={!hasDrugs ? "方案中没有药物" : dirty ? "请先保存" : ""} onClick={() => makeRx()}>{act.is("rx") ? <Spinner size={14} /> : <FilePlus2 size={14} />}生成处方草稿<ArrowRight size={14} /></button>
            )}
          </div></div>)}
      {!editable && <div className="flex justify-end"><button className="btn-primary" onClick={() => ws.go("rx")}>查看处方<ArrowRight size={15} /></button></div>}

      <Modal open={regenOpen} onClose={() => setRegenOpen(false)} title="重新生成治疗方案" footer={<><button className="btn-secondary" onClick={() => setRegenOpen(false)}>取消</button><button className="btn-primary" disabled={genBusy || !secs.length} onClick={async () => { await generate("重新生成"); setRegenOpen(false); }}>{genBusy ? <Spinner size={14} /> : <Sparkles size={14} />}重新生成</button></>}>
        <Banner kind="warn" title="当前方案会被取代">{dirty ? "您有未保存的修改，它们将丢失。" : "您对当前方案所做的修改不会带入新方案；"}当前版本会作为历史保留（v{plan.version_no}），已由它生成的处方不受影响。</Banner>
        <div className="mt-3 text-sm text-ink-600">选择需要起草的部分：</div><div className="mt-2">{picker}</div>
      </Modal>
      <RebaseModal open={rebaseOpen} onClose={() => setRebaseOpen(false)} plan={plan} />
    </div>
  );
}

function Sec({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return <div><div className="mb-1 flex items-baseline gap-2"><label className="text-xs font-medium text-ink-500">{title}</label>{hint && <span className="text-[11px] text-ink-300">{hint}</span>}</div>{children}</div>;
}

function RebaseModal({ open, onClose, plan }: { open: boolean; onClose: () => void; plan: any }) {
  const ws = useWs(); const act = useAction(); const [reason, setReason] = useState("");
  return (
    <Modal open={open} onClose={onClose} title="仍沿用这份治疗方案" footer={<><button className="btn-secondary" onClick={onClose}>取消</button>
      <button className="btn-primary" disabled={act.any || reason.trim().length < 5} onClick={() => act.run("rebase", async () => { await api.post(`/plans/${plan.id}/rebase`, { reason: reason.trim(), expected_version: plan.row_version }); setReason(""); onClose(); await ws.reload(); }, { ok: "已沿用，请重新确认方案", onStale: ws.reload })}>{act.is("rebase") ? <Spinner size={14} /> : null}确认沿用</button></>}>
      <p className="text-sm leading-relaxed text-ink-700">诊断或临床资料在方案生成后有变化，但您判断<b>本方案仍然适用</b>。方案内容（包括您的全部修改）保持不变，系统会按当前诊断/资料重新对齐，并退回“草稿”——您需要重新确认。</p>
      <label className="label mt-3">为什么仍然适用？（必填，至少 5 个字，将记入审计）</label>
      <textarea className="input min-h-[84px]" autoFocus value={reason} onChange={(e) => setReason(e.target.value)} placeholder="例如：新增的主诉与本方案无关；新增的检验结果在正常范围…" />
    </Modal>);
}

// ====================================================================================== safety preview
function SafetyPreview({ loading, error, summary, groups, dirty, onRetry }: { loading: boolean; error: any; summary?: Record<string, number>; groups: any[]; dirty: boolean; onRetry: () => void }) {
  const all: (Issue & { g: string })[] = groups.flatMap((g) => g.issues.map((i: Issue) => ({ ...i, g: g.label })));
  const total = (summary?.block || 0) + (summary?.critical || 0) + (summary?.warning || 0);
  if (!loading && !error && groups.length > 0 && all.length === 0) {
    return (
      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-jade-200 bg-jade-50/60 px-3.5 py-2 text-xs text-jade-800">
        <ShieldAlert size={15} className="text-jade-600" />
        <span className="font-semibold">用药安全预检通过：</span>
        <span>未发现规则可识别的风险（过敏、妊娠、相互作用、剂量范围、十八反/十九畏等）</span>
        {dirty && <span className="ml-auto text-amber-700">有未保存修改</span>}
      </div>
    );
  }
  return (
    <Card title="用药安全预检（规则引擎）" icon={<ShieldAlert size={16} className={total ? "text-amber-600" : "text-jade-600"} />} tone={(summary?.critical || summary?.block) ? "err" : total ? "warn" : undefined}
      right={<>{!loading && !error && <IssueBadge summary={summary} />}</>}>
      {loading ? <div className="flex items-center gap-2 text-sm text-ink-400"><Spinner size={14} />检查中…</div> : error ? <div className="text-sm text-danger-600">预检失败：{errMsg(error)} <button className="underline" onClick={onRetry}>重试</button></div> : groups.length === 0 ? <div className="text-sm text-ink-400">方案中还没有药物，无需预检。</div> : all.length === 0 ? <div className="text-sm text-jade-700">未发现规则可识别的风险（过敏、妊娠、相互作用、剂量范围、十八反/十九畏等）。</div> : (
        <ul className="space-y-1.5">{all.map((i) => <li key={i.g + i.key} className="flex flex-wrap items-start gap-2 text-[13px]"><span className={SEV[i.severity].cls}>{SEV[i.severity].label}</span><span className="text-xs text-ink-400">{i.g}</span><span className="min-w-0 flex-1 text-ink-800">{i.message}</span></li>)}</ul>)}
      {dirty && <div className="mt-2 text-xs text-amber-700">预检基于已保存的内容；保存修改后自动刷新。</div>}
      <div className="mt-2 text-[11px] leading-relaxed text-ink-400">这是规则提示，<b>不能替代医师与药师的判断</b>：规则库有限，未命中不代表安全。签署处方时会对每张处方再次校验。</div>
    </Card>);
}

// ====================================================================================== therapy items
function TherapyCard({ plan, editable }: { plan: any; editable: boolean }) {
  const ws = useWs(); const act = useAction();
  const items: any[] = plan.therapy_items || [];
  const [adding, setAdding] = useState(false); const [f, setF] = useState({ category: "针灸", name: "", points: "", indications: "", contra: "" }); const [err, setErr] = useState("");
  const add = () => {
    if (!f.name.trim()) return setErr("请填写项目名称"); setErr("");
    act.run("th-add", async () => { await api.post(`/plans/${plan.id}/therapy-items`, { category: f.category, name: f.name.trim(), acupoints: f.points.split(/[、,，\s]+/).filter(Boolean), indications: f.indications, contraindications: f.contra }); setF({ ...f, name: "", points: "", indications: "", contra: "" }); setAdding(false); await ws.reload(); }, { ok: "已添加" });
  };
  const pendingItems = items.filter((t) => t.status === "pending");
  const pending = pendingItems.length;
  const acceptAllPending = () => act.run("th-accept-all", async () => {
    for (const t of pendingItems) await api.post(`/therapy-items/${t.id}/review`, { action: "accept" });
    await ws.reload();
  }, { ok: `已接受全部 ${pending} 项治疗项目`, onStale: ws.reload });
  return (
    <Card title={<>针灸 / 推拿 / 理疗等治疗项目{pending > 0 && <span className="ml-2 tag-warn">{pending} 项待审核</span>}</>}
      right={editable && <div className="flex flex-wrap gap-2">
        {pending > 0 && <button type="button" className="btn-success btn-sm" disabled={act.any} onClick={acceptAllPending}>{act.is("th-accept-all") ? <Spinner size={12} /> : <Check size={13} />}全部接受待审项目（{pending}）</button>}
        <button type="button" className="btn-secondary btn-sm" onClick={() => setAdding(!adding)}><Plus size={13} />添加项目</button>
      </div>}>
      {items.length === 0 && !adding && <div className="text-sm text-ink-400">本方案没有非药物治疗项目。</div>}
      <div className="space-y-3">{items.map((t) => <TherapyRow key={t.id} t={t} editable={editable} />)}</div>
      {adding && editable && (
        <form className="mt-3 grid gap-2 rounded-xl bg-ink-50 p-3 sm:grid-cols-[7rem_1fr_1fr]" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <select className="input" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} aria-label="类别">{["针灸", "推拿", "艾灸", "拔罐", "理疗", "康复训练"].map((x) => <option key={x}>{x}</option>)}</select>
          <input className="input" autoFocus placeholder="项目名称" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} aria-label="项目名称" />
          <input className="input" placeholder="穴位（针灸/推拿/艾灸），用顿号分隔" value={f.points} onChange={(e) => setF({ ...f, points: e.target.value })} aria-label="穴位" />
          <input className="input sm:col-span-1" placeholder="适用条件" value={f.indications} onChange={(e) => setF({ ...f, indications: e.target.value })} aria-label="适用条件" />
          <input className="input sm:col-span-1" placeholder="禁忌" value={f.contra} onChange={(e) => setF({ ...f, contra: e.target.value })} aria-label="禁忌" />
          <button className="btn-primary" disabled={act.any}>{act.is("th-add") ? <Spinner size={14} /> : "添加"}</button>
          {err && <div className="text-xs text-danger-600 sm:col-span-3">{err}</div>}</form>)}
    </Card>);
}

function TherapyRow({ t, editable }: { t: any; editable: boolean }) {
  const ws = useWs(); const act = useAction();
  const [edit, setEdit] = useState(false);
  const [f, setF] = useState({ name: t.name, points: (t.acupoints || []).join("、"), indications: t.indications || "", contra: t.contraindications || "", manipulation: t.detail?.manipulation || "", duration: t.detail?.duration || "", frequency: t.detail?.frequency || "", course: t.detail?.course || "" });
  const review = (body: any, ok: string) => act.run(`th-${t.id}`, async () => { await api.post(`/therapy-items/${t.id}/review`, body); await ws.reload(); }, { ok, onStale: ws.reload });
  const d = t.detail || {}; const src = SRC[t.source_type] || SRC.ai;
  const tone = t.status === "rejected" ? "opacity-60" : t.status === "pending" ? "border-amber-200" : "border-ink-100";
  return (
    <article className={`rounded-xl border p-3.5 ${tone}`}>
      <div className="flex flex-wrap items-center gap-2"><span className="tag-neutral">{t.category}</span><h3 className={`font-semibold text-ink-900 ${t.status === "rejected" ? "line-through" : ""}`}>{t.name}</h3><span className={src[1]}>{src[0]}</span><StatusChip s={t.status} />
        {t.status === "modified" && t.original?.name && <span className="text-[11px] text-ink-400">AI 原版：{t.original.name}</span>}</div>
      {!edit && (<div className="mt-2 grid gap-x-6 gap-y-1 text-[13px] sm:grid-cols-2">
        {(t.acupoints || []).length > 0 && <div><span className="text-ink-400">穴位：</span>{t.acupoints.join("、")}</div>}
        {d.location_basis && <div><span className="text-ink-400">取穴/部位依据：</span>{d.location_basis}</div>}
        {d.manipulation && <div><span className="text-ink-400">手法：</span>{d.manipulation}</div>}{d.stimulation && <div><span className="text-ink-400">刺激量：</span>{d.stimulation}</div>}
        {d.duration && <div><span className="text-ink-400">时长：</span>{d.duration}</div>}{d.frequency && <div><span className="text-ink-400">频次：</span>{d.frequency}</div>}{d.course && <div><span className="text-ink-400">疗程：</span>{d.course}</div>}
        {t.indications && <div className="sm:col-span-2"><span className="text-ink-400">适用条件：</span>{t.indications}</div>}
        {t.contraindications && <div className="sm:col-span-2 text-danger-700"><span className="text-danger-500">禁忌：</span>{t.contraindications}</div>}
        {t.rationale && <div className="sm:col-span-2 text-ink-500"><span className="text-ink-400">依据：</span>{t.rationale}</div>}</div>)}
      {(t.needs_confirmation || []).length > 0 && !edit && <div className="mt-2 rounded-lg bg-amber-50/70 p-2 text-xs text-amber-800"><b>需医师确认：</b>{t.needs_confirmation.join("；")}</div>}
      {edit && (
        <form className="mt-2 grid gap-2 sm:grid-cols-2" onSubmit={(e) => {
          e.preventDefault(); if (!f.name.trim()) return;
          review({ action: "modify", edits: { name: f.name.trim(), acupoints: f.points.split(/[、,，\s]+/).filter(Boolean), indications: f.indications, contraindications: f.contra, detail: { manipulation: f.manipulation, duration: f.duration, frequency: f.frequency, course: f.course } } }, "已修改并采纳").then(() => setEdit(false));
        }}>
          <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} aria-label="项目名称" placeholder="项目名称" /><input className="input" value={f.points} onChange={(e) => setF({ ...f, points: e.target.value })} aria-label="穴位" placeholder="穴位，顿号分隔" />
          <input className="input" value={f.manipulation} onChange={(e) => setF({ ...f, manipulation: e.target.value })} placeholder="手法" aria-label="手法" /><input className="input" value={f.duration} onChange={(e) => setF({ ...f, duration: e.target.value })} placeholder="每次时长" aria-label="时长" />
          <input className="input" value={f.frequency} onChange={(e) => setF({ ...f, frequency: e.target.value })} placeholder="频次" aria-label="频次" /><input className="input" value={f.course} onChange={(e) => setF({ ...f, course: e.target.value })} placeholder="疗程" aria-label="疗程" />
          <input className="input sm:col-span-2" value={f.indications} onChange={(e) => setF({ ...f, indications: e.target.value })} placeholder="适用条件" aria-label="适用条件" /><input className="input sm:col-span-2" value={f.contra} onChange={(e) => setF({ ...f, contra: e.target.value })} placeholder="禁忌" aria-label="禁忌" />
          <div className="flex gap-2 sm:col-span-2"><button className="btn-primary btn-sm" disabled={act.any}>采纳修改后的项目</button><button type="button" className="btn-ghost btn-sm" onClick={() => setEdit(false)}>取消</button></div></form>)}
      {editable && !edit && (
        <div className="mt-3 flex flex-wrap gap-2 border-t border-ink-50 pt-3">
          {t.status !== "accepted" && t.status !== "modified" && <button type="button" className="btn-success btn-sm" disabled={act.any} onClick={() => review({ action: "accept" }, "已接受")}><Check size={13} />接受</button>}
          <button type="button" className="btn-secondary btn-sm" disabled={act.any} onClick={() => setEdit(true)}><Pencil size={13} />修改</button>
          {t.status !== "rejected" && <button type="button" className="btn-danger-ghost btn-sm" disabled={act.any} onClick={() => review({ action: "reject" }, "已拒绝")}><X size={13} />拒绝</button>}
          {t.status !== "pending" && <button type="button" className="btn-ghost btn-sm" disabled={act.any} onClick={() => review({ action: "revert" }, "已恢复为待审核")}><Undo2 size={13} />恢复待审</button>}
        </div>)}
    </article>);
}
