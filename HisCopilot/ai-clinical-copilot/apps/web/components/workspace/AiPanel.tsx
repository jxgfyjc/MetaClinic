"use client";
import { useMemo, useState } from "react";
import { AlertTriangle, ArrowRight, Check, CheckCircle2, ChevronDown, ChevronUp, CircleDashed, HelpCircle, Pencil, Plus, RefreshCw, Sparkles, Star, Undo2, X, XCircle } from "lucide-react";
import { api, ApiError, fmtTime } from "@/lib/api";
import { Banner, Empty, Spinner, StatusChip, useConfirm } from "@/components/ui";
import { useAction } from "@/lib/useAction";
import { Bullets, Card, Claim, Collapse, ConfChip, Elapsed, FactMap, SRC_LABEL, Working, factMap } from "./bits";
import { useWs } from "./ctx";

const MOD_LABEL: Record<string, string> = { tongue: "舌象观察", western: "西医分析", tcm: "中医辨证", synthesis: "综合评估" };
const MOD_ORDER = ["tongue", "western", "tcm", "synthesis"];
const ERR_HINT: Record<string, string> = {
  insufficient_facts: "临床资料过少：请先填写主诉/现病史，或上传语音、检验单后再分析",
  all_modules_failed: "所有分析模块都失败了：多半是模型服务暂时不可用，可稍后重试；也可以跳过 AI，直接手工添加诊断",
  model_oom: "显存繁忙，请稍后重试", model_http_error: "模型服务返回错误", bad_output: "模型输出格式异常（已自动重试仍失败）",
};

export function AiPanel() {
  const ws = useWs(); const { b, eid, readOnly } = ws; const an = b.analysis; const act = useAction(); const confirm = useConfirm();
  const facts = useMemo(() => factMap(an?.facts), [an]);
  const mods: Record<string, any> = useMemo(() => Object.fromEntries((an?.modules || []).map((m: any) => [m.module, m])), [an]);
  const running = an && (an.status === "queued" || an.status === "running");
  const done = an && (an.status === "succeeded" || an.status === "partial");
  const finals = b.diagnoses.filter((d) => d.status === "accepted" || d.status === "modified");
  const pending = b.diagnoses.filter((d) => d.status === "pending");
  const rejected = b.diagnoses.filter((d) => d.status === "rejected");
  const hasData = !!(b.encounter.chief_complaint || "").trim();

  const run = (force: boolean) => act.run("analyze", async () => {
    await ws.ensureStarted();
    const r = await api.post(`/encounters/${eid}/analysis`, { force });
    if (r.reused) { /* server says nothing new to do */ }
    await ws.reload();
    return r;
  }, { onStale: ws.reload }).then((r) => r);

  const [reuseMsg, setReuseMsg] = useState("");
  const start = async (force: boolean) => {
    if (force && done) { const c = await confirm({ title: "重新运行 AI 分析？", body: "将用当前的全部资料重新分析，耗时约 1–2 分钟。您已经审核过的诊断（接受/修改/驳回）不会被改动；尚未审核的旧候选会被新候选取代。", confirmText: "重新分析" }); if (!c.ok) return; }
    const r = await run(force); if (r?.reused) setReuseMsg(r.reason || ""); else setReuseMsg("");
  };

  return (
    <div className="space-y-4">
      {/* ---------------------------------------------------------------- status / control bar */}
      {!an && (
        <Card><Empty icon={<Sparkles size={22} />} title="还没有运行 AI 预分析" hint={hasData ? "AI 将阅读本次就诊的全部资料（病史、语音转写、检验、舌象），给出西医与中医各 2–4 个鉴别诊断及依据。耗时约 1–2 分钟，后台运行，您可以继续其他操作。" : "请先在“接诊资料”中填写主诉等信息。也可以不用 AI，直接在下方手工添加诊断。"}
          action={!readOnly && <button className="btn-primary mt-2" disabled={act.any || !hasData} onClick={() => start(false)}>{act.is("analyze") ? <Spinner size={15} /> : <Sparkles size={15} />}开始 AI 预分析</button>} /></Card>)}

      {running && (
        <Card tone="warn"><Working label={an.status === "queued" ? "AI 预分析排队中…" : "AI 预分析进行中…"} sub={<>已用时 <Elapsed since={an.started_at || an.created_at} />　· 后台运行，可离开本页，结果会自动保存</>} />
          <ol className="mt-3 grid gap-2 sm:grid-cols-4">{MOD_ORDER.map((m) => <ModStep key={m} name={m} mod={mods[m]} />)}</ol>
          {b.active_jobs.find((j) => j.kind === "analysis")?.message && <div className="mt-2 text-xs text-ink-500">{b.active_jobs.find((j) => j.kind === "analysis")!.message}</div>}</Card>)}

      {an && an.status === "failed" && (
        <Card tone="err"><div className="flex items-start gap-3"><XCircle className="mt-0.5 shrink-0 text-danger-600" size={20} />
          <div className="min-w-0 flex-1"><div className="font-medium text-danger-700">AI 预分析失败</div>
            <div className="mt-1 text-sm text-ink-700">{ERR_HINT[an.error_code] || "分析未能完成。"}</div>
            {an.error_detail && <div className="mt-1 break-words rounded bg-danger-50 p-2 font-mono text-[11px] text-danger-700">{an.error_code && `[${an.error_code}] `}{an.error_detail}</div>}
            <ol className="mt-3 grid gap-2 sm:grid-cols-4">{MOD_ORDER.map((m) => <ModStep key={m} name={m} mod={mods[m]} />)}</ol></div>
          {!readOnly && <button className="btn-primary shrink-0" disabled={act.any} onClick={() => start(true)}>{act.is("analyze") ? <Spinner size={14} /> : <RefreshCw size={14} />}重试分析</button>}</div></Card>)}

      {an && an.status === "cancelled" && <Banner kind="warn" title="上一次分析已取消" action={!readOnly && <button className="btn-secondary btn-sm" onClick={() => start(true)}>重新分析</button>} />}

      {done && (
        <div className="space-y-2">
          {b.staleness.analysis_stale && (
            <Banner kind="warn" title="临床资料在分析之后有变化" action={!readOnly && <button className="btn-primary btn-sm" disabled={act.any} onClick={() => start(false)}>{act.is("analyze") ? <Spinner size={13} /> : <RefreshCw size={13} />}用最新资料重新分析</button>}>
              下面的 AI 结果基于旧的资料（如新增了检验、修改了病史或过敏史）。请重新分析后再据此做决定。</Banner>)}
          {an.status === "partial" && (
            <Banner kind="warn" title="部分模块未完成" action={!readOnly && <button className="btn-secondary btn-sm" onClick={() => start(true)}>重新分析</button>}>
              {an.modules.filter((m: any) => m.status === "failed").map((m: any) => `${MOD_LABEL[m.module]}：${m.error_detail || m.error_code}`).join("；")}。已完成的部分仍可使用。</Banner>)}
          {reuseMsg && <Banner kind="info">{reuseMsg}</Banner>}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-xs text-ink-400">
            <span>分析时间 {fmtTime(an.finished_at)}</span><span>基于资料版本 v{an.inputs_version}</span>
            <span title={JSON.stringify(an.models)}>模型：{Object.values(an.models || {}).filter((v, i, a) => v && a.indexOf(v) === i).join(" / ") || "—"}</span>
            {!readOnly && <button type="button" className="ml-auto inline-flex items-center gap-1 text-brand-600 hover:underline" onClick={() => start(true)}><RefreshCw size={11} />重新分析</button>}
          </div>
        </div>)}

      {/* ---------------------------------------------------------------- layer 1: what to look at first */}
      {done && <Synthesis summary={an.summary} facts={facts} />}

      {/* ---------------------------------------------------------------- final diagnoses (doctor-owned) */}
      <FinalDiagnoses finals={finals} pendingList={pending} readOnly={readOnly} />

      {/* ---------------------------------------------------------------- layer 2: candidates */}
      {(pending.length > 0 || rejected.length > 0 || done) && (
        <Card title={<>AI 候选诊断{pending.length > 0 && <span className="ml-2 tag-warn">{pending.length} 个待审核</span>}</>} icon={<Sparkles size={16} className="text-brand-600" />}
          right={<span className="hint">AI 草稿，供医师参考；每条都可核对引用的原始资料</span>}>
          {pending.length === 0 ? <div className="rounded-lg bg-jade-50/60 px-3 py-2 text-sm text-jade-700">所有候选诊断都已审核。</div> : (
            <div className="grid gap-4 xl:grid-cols-2">
              {(["western", "tcm"] as const).map((m) => {
                const list = pending.filter((d) => d.module === m); return (
                  <div key={m} className="space-y-3"><div className="flex items-center gap-2 text-[13px] font-semibold text-ink-700"><span className={`h-2 w-2 rounded-full ${m === "western" ? "bg-brand-500" : "bg-jade-500"}`} />{m === "western" ? "西医诊断候选" : "中医证候候选"}<span className="text-ink-300">· {list.length}</span></div>
                    {list.length === 0 ? <div className="rounded-lg border border-dashed border-ink-200 p-3 text-xs text-ink-400">{mods[m]?.status === "failed" ? `该模块失败：${mods[m].error_detail || mods[m].error_code}` : "没有待审核的候选"}</div> :
                      list.map((d) => <DxCard key={d.id} d={d} facts={facts} readOnly={readOnly} />)}</div>);
              })}
            </div>)}
          {rejected.length > 0 && <div className="mt-4"><Collapse title={<>已驳回的候选（{rejected.length}）</>}><ul className="space-y-1.5">{rejected.map((d) => <RejectedRow key={d.id} d={d} readOnly={readOnly} />)}</ul></Collapse></div>}
        </Card>)}

      {/* ---------------------------------------------------------------- layer 3: details */}
      {done && <Details an={an} mods={mods} facts={facts} />}

      <div className="flex justify-end">{finals.length > 0 ? <button className="btn-primary btn-lg" onClick={() => ws.go("plan")}>下一步：制定治疗方案<ArrowRight size={16} /></button>
        : <div className="text-xs text-ink-400">至少接受或手工添加一个诊断后，才能制定治疗方案</div>}</div>
    </div>
  );
}

function ModStep({ name, mod }: { name: string; mod?: any }) {
  const s = mod?.status || "pending";
  const icon = s === "succeeded" ? <CheckCircle2 size={15} className="text-jade-600" /> : s === "running" ? <Spinner size={14} className="text-brand-600" /> : s === "failed" ? <XCircle size={15} className="text-danger-600" /> : s === "skipped" ? <CircleDashed size={15} className="text-ink-300" /> : <CircleDashed size={15} className="text-ink-300" />;
  return (
    <li className={`rounded-lg border px-2.5 py-2 text-xs ${s === "failed" ? "border-danger-100 bg-danger-50/50" : "border-ink-100 bg-white"}`}>
      <div className="flex items-center gap-1.5 font-medium text-ink-700">{icon}{MOD_LABEL[name]}</div>
      <div className="mt-0.5 text-[11px] text-ink-400">{s === "succeeded" ? `完成${mod?.duration_ms ? ` · ${(mod.duration_ms / 1000).toFixed(0)}s` : ""}` : s === "running" ? "进行中" : s === "failed" ? (mod?.error_detail || "失败") : s === "skipped" ? (mod?.error_detail || "已跳过") : "等待中"}</div>
    </li>);
}

// ------------------------------------------------------------------------------------------ synthesis
function Synthesis({ summary, facts }: { summary: any; facts: FactMap }) {
  const s = summary || {};
  const empty = !s.headline && !(s.key_decisions || []).length && !(s.confirm_first || []).length && !(s.risk_alerts || []).length;
  if (empty) return <Banner kind="info" title="综合评估未生成">西医/中医分析结果仍可在下方查看。</Banner>;
  const hasSecondary = (s.confirm_first || []).length > 0 || (s.key_decisions || []).length > 0 || (s.conflicts || []).length > 0 || (s.coordination_notes || []).length > 0;
  return (
    <Card title="综合评估摘要" icon={<Sparkles size={16} className="text-brand-600" />} className="border-brand-100 bg-gradient-to-br from-brand-50/60 to-white" right={<span className="tag-neutral">AI 综合评估 · 仅供参考</span>}>
      {s.headline && <p className="text-[14.5px] font-medium leading-relaxed text-ink-900">{s.headline}</p>}
      {(s.risk_alerts || []).length > 0 && (
        <div className="mt-2.5 rounded-xl border border-danger-100 bg-danger-50/60 p-2.5">
          <div className="mb-1 flex items-center gap-1.5 text-[12.5px] font-semibold text-danger-700"><AlertTriangle size={14} />风险提示（来自资料中的危险信号）</div>
          <div className="space-y-1">{s.risk_alerts.map((r: any, i: number) => <Claim key={i} text={r.text} refs={r.refs} facts={facts} />)}</div>
        </div>
      )}
      {(s.suggested_diagnosis_set || []).length > 0 && (
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5 rounded-lg bg-white/80 px-2.5 py-2 text-xs text-ink-600"><b className="text-ink-700">AI 建议诊断组合（仅供参考）：</b>
          {s.suggested_diagnosis_set.map((x: any, i: number) => <span key={i} className="inline-flex items-center"><span className={x.module === "tcm" ? "tag-ok" : "tag-brand"}>{x.name}</span>{x.note ? <span className="ml-1 text-ink-400">{x.note}</span> : null}</span>)}</div>)}
      {hasSecondary && (
        <div className="mt-2.5">
          <Collapse title="展开协调要点与待核实细节">
            <div className="grid gap-3 lg:grid-cols-2">
              {(s.confirm_first || []).length > 0 && <div><div className="mb-1 flex items-center gap-1.5 text-[12.5px] font-semibold text-amber-700"><HelpCircle size={14} />开始治疗前请先确认</div><Bullets items={s.confirm_first} tone="warn" /></div>}
              {(s.key_decisions || []).length > 0 && <div><div className="mb-1 text-[12.5px] font-semibold text-ink-800">本次需要您拍板的事项</div><Bullets items={s.key_decisions} /></div>}
              {(s.conflicts || []).length > 0 && <div><div className="mb-1 text-[12.5px] font-semibold text-ink-800">中西医结论不一致之处</div><Bullets items={s.conflicts} tone="warn" /></div>}
              {(s.coordination_notes || []).length > 0 && <div><div className="mb-1 text-[12.5px] font-semibold text-ink-800">中西医协调要点</div><Bullets items={s.coordination_notes} /></div>}
            </div>
          </Collapse>
        </div>
      )}
      {s.validation?.dropped_refs > 0 && <div className="mt-2 text-[11px] text-ink-400">已自动移除 {s.validation.dropped_refs} 条无法核实的引用</div>}
    </Card>
  );
}

// ------------------------------------------------------------------------------------------ final diagnoses
function FinalDiagnoses({ finals, pendingList, readOnly }: { finals: any[]; pendingList: any[]; readOnly: boolean }) {
  const ws = useWs(); const act = useAction(); const confirm = useConfirm();
  const [adding, setAdding] = useState(false); const [f, setF] = useState({ module: "western", name: "", icd: "" }); const [err, setErr] = useState("");
  const [edit, setEdit] = useState<string | null>(null); const [editName, setEditName] = useState("");
  const pendingCount = pendingList.length;
  const review = (d: any, body: any, ok?: string) => act.run(`dx-${d.id}`, async () => { await api.post(`/diagnoses/${d.id}/review`, { ...body, expected_version: d.row_version }); await ws.reload(); }, { ok, onStale: ws.reload });
  const rejectRest = () => act.run("dx-reject-rest", async () => {
    for (const d of pendingList) await api.post(`/diagnoses/${d.id}/review`, { action: "reject", expected_version: d.row_version });
    await ws.reload();
  }, { ok: `已将 ${pendingCount} 个其余待审候选标为不采纳`, onStale: ws.reload });
  const add = () => {
    if (!f.name.trim()) return setErr("请填写诊断名称"); setErr("");
    act.run("dx-add", async () => { await ws.ensureStarted(); await api.post(`/encounters/${ws.eid}/diagnoses`, { module: f.module, name: f.name.trim(), icd_code: f.icd.trim(), primary: finals.length === 0 }); setF({ ...f, name: "", icd: "" }); setAdding(false); await ws.reload(); }, { ok: "已添加诊断" });
  };
  return (
    <Card title={<>最终诊断（医师确认）<span className="ml-2 tag-neutral">{finals.length}</span></>} icon={<Check size={16} className="text-jade-600" />} className={finals.length ? "border-jade-100" : ""}
      right={<div className="flex flex-wrap items-center gap-2">
        {!readOnly && pendingCount > 0 && finals.length > 0 && <button type="button" className="btn-secondary btn-sm" disabled={act.any} onClick={rejectRest}>{act.is("dx-reject-rest") ? <Spinner size={12} /> : <X size={13} />}其余待审标为不采纳（{pendingCount}）</button>}
        {!readOnly && <button type="button" className="btn-secondary btn-sm" onClick={() => setAdding(!adding)}><Plus size={13} />添加我的诊断</button>}
        {finals.length > 0 && <button type="button" className="btn-primary btn-sm" onClick={() => ws.go("plan")}>下一步：制定治疗方案 →</button>}
      </div>}>
      {finals.length === 0 && !adding && <div className="text-sm text-ink-500">尚未确认任何诊断。请在下方审核 AI 候选（接受 / 修改 / 驳回），或点击“添加我的诊断”。治疗方案将基于这里的<b>全部</b>诊断统一制定。</div>}
      {pendingCount > 0 && finals.length > 0 && <div className="mb-2 text-xs text-amber-700">还有 {pendingCount} 个候选诊断未审核。未审核的候选不会进入治疗方案；定稿病历前需全部处理。</div>}
      {finals.length > 0 && (
        <ul className="divide-y divide-ink-50">{finals.map((d) => (
          <li key={d.id} className="flex flex-wrap items-center gap-2 py-2.5">
            <span className={d.module === "tcm" ? "tag-ok" : "tag-brand"}>{d.module === "tcm" ? "中医" : "西医"}</span>
            {edit === d.id ? (
              <form className="flex flex-1 items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (editName.trim()) review(d, { action: "modify", name: editName.trim() }, "已修改诊断").then(() => setEdit(null)); }}>
                <input className="input py-1" autoFocus value={editName} onChange={(e) => setEditName(e.target.value)} aria-label="诊断名称" /><button className="btn-primary btn-sm">保存</button><button type="button" className="btn-ghost btn-sm" onClick={() => setEdit(null)}>取消</button></form>
            ) : (<>
              <span className="font-medium text-ink-900">{d.name}</span>
              {d.is_primary && <span className="tag-warn"><Star size={10} fill="currentColor" />主要诊断</span>}
              {d.status === "modified" && <span className="tag-neutral" title={`AI 原始名称：${d.original_name}`}>已修改{d.original_name ? `（AI：${d.original_name}）` : ""}</span>}
              {d.origin === "doctor" && <span className="tag-neutral">医师添加</span>}
              {d.icd_code && <span className="kbd">{d.icd_code}</span>}
              {!readOnly && (
                <span className="ml-auto flex flex-wrap items-center gap-1">
                  {!d.is_primary && <button type="button" className="btn-ghost btn-sm" disabled={act.any} onClick={() => review(d, { action: d.status === "modified" ? "modify" : "accept", name: d.name, primary: true }, "已设为主要诊断")}><Star size={12} />设为主要</button>}
                  <button type="button" className="btn-ghost btn-sm" disabled={act.any} onClick={() => { setEdit(d.id); setEditName(d.name); }}><Pencil size={12} />修改</button>
                  {d.origin === "doctor" ? <button type="button" className="btn-ghost btn-sm text-danger-600" disabled={act.any} onClick={async () => { const c = await confirm({ title: `撤销诊断「${d.name}」？`, body: "这是您自己添加的诊断，撤销后将从最终诊断中移除；已基于它生成的治疗方案会提示“已过期”。", confirmText: "撤销", danger: true }); if (c.ok) review(d, { action: "withdraw" }); }}><X size={12} />撤销</button>
                    : <button type="button" className="btn-ghost btn-sm" disabled={act.any} onClick={async () => { const c = await confirm({ title: `将「${d.name}」退回待审核？`, body: "退回后它不再属于最终诊断；已基于它生成的治疗方案会提示“已过期”。", confirmText: "退回" }); if (c.ok) review(d, { action: "revert" }); }}><Undo2 size={12} />退回待审</button>}
                </span>)}
            </>)}
          </li>))}</ul>)}
      {adding && !readOnly && (
        <form className="mt-3 grid gap-2 rounded-xl bg-ink-50 p-3 sm:grid-cols-[7rem_1fr_9rem_auto]" onSubmit={(e) => { e.preventDefault(); add(); }}>
          <select className="input" value={f.module} onChange={(e) => setF({ ...f, module: e.target.value })} aria-label="诊断类型"><option value="western">西医诊断</option><option value="tcm">中医证型</option></select>
          <input className="input" autoFocus placeholder="诊断名称，如：2型糖尿病 / 气阴两虚证" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} aria-label="诊断名称" />
          <input className="input" placeholder="ICD 编码（可选）" value={f.icd} onChange={(e) => setF({ ...f, icd: e.target.value })} aria-label="ICD 编码" />
          <button className="btn-primary" disabled={act.any}>{act.is("dx-add") ? <Spinner size={14} /> : "添加"}</button>
          {err && <div className="text-xs text-danger-600 sm:col-span-4">{err}</div>}
        </form>)}
    </Card>
  );
}

// ------------------------------------------------------------------------------------------ candidate card
function DxCard({ d, facts, readOnly }: { d: any; facts: FactMap; readOnly: boolean }) {
  const ws = useWs(); const act = useAction(); const [open, setOpen] = useState(false); const [mod, setMod] = useState(false); const [name, setName] = useState(d.name); const [note, setNote] = useState("");
  const ev = d.evidence || {}; const review = (body: any, ok: string) => act.run(`dx-${d.id}`, async () => { await api.post(`/diagnoses/${d.id}/review`, { ...body, expected_version: d.row_version }); await ws.reload(); }, { ok, onStale: ws.reload });
  const sup = ev.supporting || [], con = ev.contradicting || [];
  return (
    <article className="rounded-xl border border-ink-100 bg-white p-3.5 shadow-card transition hover:border-ink-200">
      <div className="flex flex-wrap items-start gap-2">
        <h3 className="min-w-0 flex-1 text-[15px] font-semibold leading-snug text-ink-900">{d.name}</h3><ConfChip c={d.confidence} />
      </div>
      {d.rationale && <p className={`mt-1.5 text-[13px] leading-relaxed text-ink-600 ${open ? "" : "line-clamp-2"}`}>{d.rationale}</p>}
      {ev.evidence_weak && <div className="mt-2"><span className="tag-warn"><AlertTriangle size={11} />依据较弱：模型未给出可核实的支持引用，请重点核对</span></div>}
      <div className="mt-2.5 space-y-2.5">
        {sup.length > 0 && <div><div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-jade-700">支持依据</div><div className="space-y-1">{sup.slice(0, open ? 99 : 1).map((c: any, i: number) => <Claim key={i} text={c.text} refs={c.refs} facts={facts} />)}</div></div>}
        {open && con.length > 0 && <div><div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-danger-600">不支持 / 矛盾之处</div><div className="space-y-1">{con.map((c: any, i: number) => <Claim key={i} text={c.text} refs={c.refs} facts={facts} tone="against" />)}</div></div>}
        {open && (ev.missing_information || []).length > 0 && <div><div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-amber-700">要确认该诊断，还缺少</div><Bullets items={ev.missing_information} tone="warn" /></div>}
        {open && (ev.questions_to_ask || []).length > 0 && <div><div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-brand-700">建议追问患者</div><Bullets items={ev.questions_to_ask} /></div>}
      </div>
      {(sup.length > 2 || con.length > 0 || (ev.missing_information || []).length > 0 || (ev.questions_to_ask || []).length > 0) &&
        <button type="button" className="mt-2 inline-flex items-center gap-1 text-xs text-brand-600 hover:underline" onClick={() => setOpen(!open)}>{open ? <><ChevronUp size={13} />收起依据</> : <><ChevronDown size={13} />展开全部依据 / 缺失信息 / 追问建议</>}</button>}
      {!readOnly && (mod ? (
        <form className="mt-3 space-y-2 rounded-lg bg-ink-50 p-2.5" onSubmit={(e) => { e.preventDefault(); if (name.trim()) review({ action: "modify", name: name.trim(), note: note.trim() || undefined }, "已修改并采纳").then(() => setMod(false)); }}>
          <div className="text-xs text-ink-500">修改后的名称将作为最终诊断；AI 原始名称会保留在记录中。</div>
          <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} aria-label="诊断名称" /><input className="input" placeholder="修改说明（可选）" value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="flex gap-2"><button className="btn-primary btn-sm" disabled={act.any || !name.trim()}>采纳修改后的诊断</button><button type="button" className="btn-ghost btn-sm" onClick={() => setMod(false)}>取消</button></div></form>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2 border-t border-ink-50 pt-3">
          <button type="button" className="btn-success btn-sm" disabled={act.any} onClick={() => review({ action: "accept" }, "已接受")}>{act.is(`dx-${d.id}`) ? <Spinner size={12} /> : <Check size={13} />}接受</button>
          <button type="button" className="btn-secondary btn-sm" disabled={act.any} onClick={() => { setName(d.name); setMod(true); }}><Pencil size={13} />修改</button>
          <button type="button" className="btn-danger-ghost btn-sm" disabled={act.any} onClick={() => review({ action: "reject" }, "已驳回")}><X size={13} />驳回</button>
        </div>))}
    </article>
  );
}

function RejectedRow({ d, readOnly }: { d: any; readOnly: boolean }) {
  const ws = useWs(); const act = useAction();
  return <li className="flex items-center gap-2 text-[13px] text-ink-500"><span className={d.module === "tcm" ? "tag-ok" : "tag-brand"}>{d.module === "tcm" ? "中医" : "西医"}</span><span className="line-through">{d.name}</span>
    {!readOnly && <button type="button" className="ml-auto btn-ghost btn-sm" disabled={act.any} onClick={() => act.run(`dx-${d.id}`, async () => { await api.post(`/diagnoses/${d.id}/review`, { action: "revert", expected_version: d.row_version }); await ws.reload(); }, { ok: "已恢复为待审核", onStale: ws.reload })}><Undo2 size={12} />恢复</button>}</li>;
}

// ------------------------------------------------------------------------------------------ details layer
function Details({ an, mods, facts }: { an: any; mods: Record<string, any>; facts: FactMap }) {
  const w = mods.western?.output || {}, t = mods.tcm?.output || {}, tg = mods.tongue?.output;
  const redFlags = [...(w.red_flags || [])];
  const exams = w.suggested_examinations || [];
  const unc = [...(w.uncertainties || []), ...(t.uncertainties || [])];
  const missing = [...(w.missing_information || []), ...(t.missing_information || []), ...(t.missing_four_diagnoses || []).map((x: string) => `中医四诊缺：${x}`)];
  const four = t.four_diagnoses || {};
  const fourRows: [string, any[]][] = [["望", four.wang], ["闻", four.wen], ["问", four.wen_ask], ["切", four.qie]];
  return (
    <div className="space-y-3">
      {redFlags.length > 0 && <Card tone="err" title={<span className="flex items-center gap-1.5 text-danger-700"><AlertTriangle size={16} />需要警惕的危险信号</span>}><div className="space-y-1.5">{redFlags.map((r: any, i: number) => <Claim key={i} text={r.text} refs={r.refs} facts={facts} />)}</div></Card>}
      <Collapse title="展开补充提示、四诊整理与原始事实溯源">
        <div className="space-y-3">
          <div className="grid gap-3 lg:grid-cols-2">
            {missing.length > 0 && <Card title="AI 认为仍缺少的信息"><Bullets items={missing} tone="warn" /></Card>}
            {unc.length > 0 && <Card title="不确定性说明"><Bullets items={unc} /></Card>}
            {exams.length > 0 && <Card title="建议完善的检查"><Bullets items={exams} /></Card>}
            {(t.treatment_principles || []).length > 0 && <Card title="中医治法方向（仅原则，具体方药在治疗方案中制定）"><Bullets items={t.treatment_principles} />
              {(t.formula_directions || []).length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{t.formula_directions.map((f: any, i: number) => <span key={i} className="tag-ok" title={f.note}>{f.name}</span>)}</div>}</Card>}
          </div>
          {(mods.tcm?.status === "succeeded") && fourRows.some(([, v]) => (v || []).length) && (
            <Collapse title="中医四诊整理（仅来自医师记录/患者自述的真实内容）"><dl className="space-y-2">{fourRows.map(([k, v]) => (v || []).length > 0 && <div key={k} className="flex gap-3"><dt className="w-6 shrink-0 font-semibold text-ink-700">{k}</dt><dd className="min-w-0 flex-1 space-y-1">{v.map((c: any, i: number) => <Claim key={i} text={c.text} refs={c.refs} facts={facts} />)}</dd></div>)}</dl></Collapse>)}
          {tg && <Collapse title="舌象观察（图像模型）"><Bullets items={tg.observations} />{(tg.uncertain || []).length > 0 && <div className="mt-1 text-xs text-amber-700">不确定：{tg.uncertain.join("；")}</div>}</Collapse>}
          <Collapse title={<>AI 使用的原始资料（事实表 F1–F{(an.facts || []).length}）</>} right={<span className="hint">AI 只能引用这里列出的内容</span>}>
            <div className="overflow-x-auto"><table className="tbl text-[13px]"><thead><tr><th>编号</th><th>项目</th><th>内容</th><th>来源</th><th>可靠性</th></tr></thead>
              <tbody>{(an.facts || []).map((f: any) => <tr key={f.id}><td><span className="kbd">{f.id}</span></td><td className="whitespace-nowrap font-medium">{f.label}</td><td className="whitespace-pre-wrap">{f.text}</td><td className="whitespace-nowrap text-ink-500">{SRC_LABEL[f.source] || f.source}</td><td className="text-xs text-amber-700">{f.reliability}</td></tr>)}</tbody></table></div>
          </Collapse>
          <Collapse title="运行详情（模块状态 / 耗时 / 校验）">
            <div className="overflow-x-auto"><table className="tbl text-[13px]"><thead><tr><th>模块</th><th>状态</th><th>模型</th><th>耗时</th><th>引用校验</th><th>备注</th></tr></thead>
              <tbody>{(an.modules || []).map((m: any) => <tr key={m.module}><td>{MOD_LABEL[m.module] || m.module}</td><td><StatusChip s={m.status} /></td><td className="font-mono text-xs">{m.model || "—"}</td><td>{m.duration_ms ? `${(m.duration_ms / 1000).toFixed(1)}s` : "—"}</td>
                <td className="text-xs">{m.output?.validation ? (m.output.validation.dropped_refs ? `移除 ${m.output.validation.dropped_refs} 条无效引用` : "全部引用有效") : "—"}</td><td className="text-xs text-danger-600">{m.error_detail}</td></tr>)}</tbody></table></div>
            {(an.modules || []).filter((m: any) => m.output?.failure_raw_excerpt).map((m: any) => (
              <details key={m.module} className="mt-2 text-xs"><summary className="cursor-pointer text-ink-500">{MOD_LABEL[m.module] || m.module}失败时模型实际返回的内容（节选，供排查）</summary>
                <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-ink-50 p-2 text-ink-600">{m.output.failure_raw_excerpt}</pre></details>))}
            <div className="mt-2 text-[11px] text-ink-400">提示词版本：{Object.entries(an.prompt_versions || {}).map(([k, v]) => `${k}=${v}`).join("，") || "—"}。每条引用都会校验是否存在于事实表，伪造的引用会被自动移除。</div>
          </Collapse>
        </div>
      </Collapse>
    </div>
  );
}
