"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Plus, Save, X } from "lucide-react";
import { api, ApiError, errMsg, fmtTime } from "@/lib/api";
import { Banner, Field, Spinner, useUnsavedGuard } from "@/components/ui";
import { Card, Collapse, Seg, sameJson } from "./bits";
import { useWs } from "./ctx";

type Tri = boolean | null;
type Form = {
  chief_complaint: string; hpi: string; past_history: string; personal_family_history: string; physical_exam: string;
  symptoms: string[]; current_meds: { name: string; dose: string; frequency: string }[]; vitals: Record<string, string>;
  special_status: { pregnant: Tri; lactating: Tri; renal: Tri; hepatic: Tri }; tcm_four: Record<string, string>;
};
type Key = keyof Form;
const KEYS: Key[] = ["chief_complaint", "hpi", "past_history", "personal_family_history", "physical_exam", "symptoms", "current_meds", "vitals", "special_status", "tcm_four"];
const LABEL: Record<Key, string> = { chief_complaint: "主诉", hpi: "现病史", past_history: "既往史", personal_family_history: "个人史 / 家族史", physical_exam: "体格检查", symptoms: "伴随症状", current_meds: "当前用药", vitals: "生命体征", special_status: "特殊状态", tcm_four: "中医四诊" };
const VITALS: [string, string][] = [["体温", "℃"], ["脉搏", "次/分"], ["呼吸", "次/分"], ["血压", "mmHg"], ["血氧饱和度", "%"]];
const TCM: [string, string][] = [["望", "神色、形态"], ["闻", "声音、气味"], ["问", "寒热、汗、饮食、二便、睡眠"], ["切", "脉象以外的切诊"], ["舌象", "舌质、舌苔（也可上传舌象图）"], ["脉象", "如：脉弦细"]];
const SPECIAL: [keyof Form["special_status"], string][] = [["pregnant", "妊娠"], ["lactating", "哺乳期"], ["renal", "肾功能不全"], ["hepatic", "肝功能不全"]];

const fromEnc = (e: any): Form => ({
  chief_complaint: e.chief_complaint || "", hpi: e.hpi || "", past_history: e.past_history || "", personal_family_history: e.personal_family_history || "", physical_exam: e.physical_exam || "",
  symptoms: e.symptoms || [], current_meds: (e.current_meds || []).map((m: any) => ({ name: m.name || "", dose: m.dose || "", frequency: m.frequency || "" })),
  vitals: { ...(e.vitals || {}) }, special_status: { pregnant: null, lactating: null, renal: null, hepatic: null, ...(e.special_status || {}) }, tcm_four: { ...(e.tcm_four || {}) },
});
/** what we actually send: trimmed / emptied-out values, so "typed a space" is not a change */
const norm = (k: Key, v: any) => {
  if (typeof v === "string") return v.trim();
  if (k === "symptoms") return v.map((s: string) => s.trim()).filter(Boolean);
  if (k === "current_meds") return v.map((m: any) => ({ name: m.name.trim(), dose: m.dose.trim(), frequency: m.frequency.trim() })).filter((m: any) => m.name);
  if (k === "vitals" || k === "tcm_four") return Object.fromEntries(Object.entries(v).map(([a, b]) => [a, String(b).trim()]).filter(([, b]) => b));
  return v;
};

export function FactsPanel() {
  const ws = useWs(); const { b, eid, readOnly } = ws; const e = b.encounter;
  const server = useMemo(() => fromEnc(e), [e]);
  const [draft, setDraft] = useState<Form>(server);
  const base = useRef<Form>(server);
  const [conflicts, setConflicts] = useState<Partial<Record<Key, any>>>({});
  const [saving, setSaving] = useState(false); const [err, setErr] = useState(""); const [savedAt, setSavedAt] = useState<string | null>(null);
  const draftRef = useRef(draft); draftRef.current = draft;
  const savingRef = useRef(false);

  const dirtyKeys = useCallback((d: Form = draftRef.current) => KEYS.filter((k) => !sameJson(norm(k, d[k]), norm(k, base.current[k]))), []);
  const dirty = dirtyKeys(draft).length > 0;
  useUnsavedGuard(dirty);

  // Merge server → form WITHOUT ever overwriting text the doctor is typing: only untouched fields follow the server.
  useEffect(() => {
    const cur = draftRef.current; const next: Form = { ...cur }; const nb: Form = { ...base.current }; const cf: Partial<Record<Key, any>> = {};
    for (const k of KEYS) {
      const touched = !sameJson(norm(k, cur[k]), norm(k, base.current[k]));
      if (!touched) { (next as any)[k] = server[k]; (nb as any)[k] = server[k]; }
      else if (!sameJson(norm(k, server[k]), norm(k, base.current[k])) && !sameJson(norm(k, server[k]), norm(k, cur[k]))) cf[k] = server[k];
      else if (sameJson(norm(k, server[k]), norm(k, cur[k]))) (nb as any)[k] = server[k];          // server caught up with what we typed
    }
    base.current = nb; setConflicts(cf);
    if (!sameJson(next, cur)) setDraft(next);
  }, [server]);

  const save = useCallback(async (quiet = false) => {
    if (savingRef.current || readOnly) return;
    const keys = dirtyKeys().filter((k) => !(k in conflicts));
    if (!keys.length) return;
    savingRef.current = true; setSaving(true); setErr("");
    const payload: any = {}; keys.forEach((k) => { payload[k] = norm(k, draftRef.current[k]); });
    try {
      await ws.ensureStarted();
      let ver = b.encounter.row_version;
      try { await api.patch(`/encounters/${eid}/facts`, { expected_version: ver, ...payload }); }
      catch (x) {
        if (!(x instanceof ApiError && x.isStale)) throw x;
        // someone (an OCR job finishing, another window) bumped the version: retry only if THEIR changes don't touch OUR fields
        const fresh = await api.get(`/encounters/${eid}`); const fs = fromEnc(fresh.encounter);
        const clash = keys.filter((k) => !sameJson(norm(k, fs[k]), norm(k, base.current[k])) && !sameJson(norm(k, fs[k]), norm(k, draftRef.current[k])));
        if (clash.length) { setConflicts((c) => ({ ...c, ...Object.fromEntries(clash.map((k) => [k, fs[k]])) })); await ws.reload(); throw new Error(`「${clash.map((k) => LABEL[k]).join("、")}」已被其他窗口修改，请选择保留哪个版本`); }
        ver = fresh.encounter.row_version; await api.patch(`/encounters/${eid}/facts`, { expected_version: ver, ...payload });
      }
      keys.forEach((k) => { (base.current as any)[k] = draftRef.current[k]; });
      setSavedAt(new Date().toISOString()); await ws.reload();
    } catch (x) { setErr(errMsg(x)); if (!quiet) { /* shown inline */ } }
    finally { savingRef.current = false; setSaving(false); }
  }, [b.encounter.row_version, conflicts, dirtyKeys, eid, readOnly, ws]);

  // autosave 2.5 s after the last keystroke
  const saveRef = useRef(save); saveRef.current = save;
  useEffect(() => {
    if (!dirty || readOnly || Object.keys(conflicts).length) return;
    const t = setTimeout(() => saveRef.current(true), 2500);
    return () => clearTimeout(t);
  }, [draft, dirty, readOnly, conflicts]);
  // flush on leaving the page (SPA navigation)
  useEffect(() => () => { if (dirtyKeys().length) saveRef.current(true); }, [dirtyKeys]);

  // let other panels (voice transcript → "append to HPI") add text without replacing anything
  useEffect(() => {
    ws.hpiAppend.current = (t: string) => setDraft((d) => ({ ...d, hpi: d.hpi.trim() ? `${d.hpi.replace(/\s+$/, "")}\n${t}` : t }));
    return () => { ws.hpiAppend.current = null; };
  }, [ws.hpiAppend]);

  const set = <K extends Key>(k: K, v: Form[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const dis = readOnly;
  const [symInput, setSymInput] = useState("");
  const addSym = () => { const t = symInput.trim(); if (t && !draft.symptoms.includes(t)) set("symptoms", [...draft.symptoms, t]); setSymInput(""); };
  const rep = e.patient_reported as any;
  const filledExtraCount =
    (draft.past_history.trim() ? 1 : 0) +
    (draft.personal_family_history.trim() ? 1 : 0) +
    (draft.physical_exam.trim() ? 1 : 0) +
    Object.values(draft.vitals).filter((v) => String(v).trim()).length +
    (["望", "闻", "问", "切"].filter((k) => (draft.tcm_four[k] || "").trim()).length);

  const Conflict = ({ k }: { k: Key }) => k in conflicts ? (
    <div className="mt-1.5 rounded-lg border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
      <b>其他窗口刚刚把这一项改成了：</b><div className="mt-1 whitespace-pre-wrap rounded bg-white/70 p-1.5 text-ink-700">{typeof conflicts[k] === "string" ? conflicts[k] || "（空）" : JSON.stringify(conflicts[k])}</div>
      <div className="mt-1.5 flex gap-2"><button type="button" className="btn-secondary btn-sm" onClick={() => { (base.current as any)[k] = conflicts[k]; setConflicts((c) => { const n = { ...c }; delete n[k]; return n; }); }}>保留我的修改</button>
        <button type="button" className="btn-secondary btn-sm" onClick={() => { set(k, conflicts[k]); (base.current as any)[k] = conflicts[k]; setConflicts((c) => { const n = { ...c }; delete n[k]; return n; }); }}>采用对方的</button></div></div>) : null;

  return (
    <div className="space-y-4">
      {rep && Object.keys(rep).length > 0 && (
        <details className="card px-4 py-3 text-sm"><summary className="cursor-pointer font-medium text-ink-700">患者自述原文（提交于 {fmtTime(rep.submitted_at)}）<span className="ml-2 text-xs font-normal text-ink-400">下方字段已据此预填，您的修改以您为准</span></summary>
          <dl className="mt-2 grid gap-x-6 gap-y-1.5 text-[13px] sm:grid-cols-2">
            {[["主诉", rep.chief_complaint], ["现病史", rep.hpi], ["伴随症状", (rep.symptoms || []).join("、")], ["过敏史", rep.no_allergies_confirmed ? "患者勾选：无药物/食物过敏" : (rep.allergies || []).join("、")]].map(([k, v]) => (
              <div key={k as string} className="flex gap-2"><dt className="w-16 shrink-0 text-ink-400">{k}</dt><dd className="whitespace-pre-wrap text-ink-800">{v || <span className="text-ink-300">未填写</span>}</dd></div>))}
          </dl></details>)}

      <Card title="核心病史与四诊" right={<SaveState dirty={dirty} saving={saving} savedAt={savedAt} readOnly={readOnly} onSave={() => save()} />}>
        {err && <div role="alert" className="mb-3 rounded-lg border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-600">{err}<button className="ml-2 underline" onClick={() => save()}>重试保存</button></div>}
        <div className="grid gap-3.5">
          <Field label="主诉" required hint="主要症状 + 持续时间"><input className="input" disabled={dis} value={draft.chief_complaint} onChange={(x) => set("chief_complaint", x.target.value)} maxLength={500} /><Conflict k="chief_complaint" /></Field>
          <Field label="现病史"><textarea className="input min-h-[80px]" disabled={dis} value={draft.hpi} onChange={(x) => set("hpi", x.target.value)} placeholder="发病经过、症状特点、加重/缓解因素、诊治经过…" /><Conflict k="hpi" /></Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="舌象"><input className="input" disabled={dis} placeholder="舌质、舌苔（也可上传舌象图）" value={draft.tcm_four["舌象"] || ""} onChange={(x) => set("tcm_four", { ...draft.tcm_four, ["舌象"]: x.target.value })} /></Field>
            <Field label="脉象"><input className="input" disabled={dis} placeholder="如：脉弦细" value={draft.tcm_four["脉象"] || ""} onChange={(x) => set("tcm_four", { ...draft.tcm_four, ["脉象"]: x.target.value })} /></Field>
          </div>
          <div>
            <label className="label">伴随症状</label>
            <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-ink-200 bg-white p-1.5 focus-within:border-brand-400 focus-within:shadow-ring">
              {draft.symptoms.map((s) => <span key={s} className="tag-brand">{s}{!dis && <button type="button" aria-label={`移除${s}`} onClick={() => set("symptoms", draft.symptoms.filter((x) => x !== s))}><X size={11} /></button>}</span>)}
              <input className="min-w-[8rem] flex-1 border-0 bg-transparent px-1.5 py-1 text-sm outline-none" disabled={dis} value={symInput} placeholder={dis ? "" : "输入后回车添加，如：口干、乏力"} onChange={(x) => setSymInput(x.target.value)}
                onKeyDown={(x) => { if (x.key === "Enter" || x.key === "，" || x.key === ",") { x.preventDefault(); addSym(); } if (x.key === "Backspace" && !symInput && draft.symptoms.length) set("symptoms", draft.symptoms.slice(0, -1)); }} onBlur={addSym} />
            </div><Conflict k="symptoms" />
          </div>
        </div>
      </Card>

      <Card title="用药安全核对" right={<span className="hint">影响处方安全校验</span>}>
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <label className="label">当前正在使用的药物</label>
            <div className="space-y-1.5">
              {draft.current_meds.map((m, i) => (
                <div key={i} className="grid grid-cols-[1fr_5.5rem_5.5rem_auto] gap-1.5">
                  <input className="input py-1.5" disabled={dis} placeholder="药名" value={m.name} onChange={(x) => set("current_meds", draft.current_meds.map((r, j) => j === i ? { ...r, name: x.target.value } : r))} />
                  <input className="input py-1.5" disabled={dis} placeholder="剂量" value={m.dose} onChange={(x) => set("current_meds", draft.current_meds.map((r, j) => j === i ? { ...r, dose: x.target.value } : r))} />
                  <input className="input py-1.5" disabled={dis} placeholder="频次" value={m.frequency} onChange={(x) => set("current_meds", draft.current_meds.map((r, j) => j === i ? { ...r, frequency: x.target.value } : r))} />
                  {!dis && <button type="button" className="btn-ghost btn-sm text-ink-400" aria-label="删除" onClick={() => set("current_meds", draft.current_meds.filter((_, j) => j !== i))}><X size={14} /></button>}
                </div>))}
              {draft.current_meds.length === 0 && <div className="rounded-lg border border-dashed border-ink-200 px-3 py-2 text-xs text-ink-400">未记录。在用药物将参与相互作用校验。</div>}
              {!dis && <button type="button" className="btn-secondary btn-sm" onClick={() => set("current_meds", [...draft.current_meds, { name: "", dose: "", frequency: "" }])}><Plus size={13} />添加药物</button>}
            </div><Conflict k="current_meds" />
          </div>
          <div>
            <label className="label">特殊状态（“未询问”会在开药时提示核对）</label>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2">{SPECIAL.map(([k, name]) => (
              <div key={k} className="flex flex-col gap-1"><span className="text-xs text-ink-700">{name}</span>
                <Seg<Tri> value={draft.special_status[k]} disabled={dis} onChange={(v) => set("special_status", { ...draft.special_status, [k]: v })}
                  options={[{ v: null, label: "未询问", tone: "bg-ink-500 text-white" }, { v: false, label: "否", tone: "bg-jade-600 text-white" }, { v: true, label: "是", tone: "bg-danger-600 text-white" }]} /></div>))}</div>
            <Conflict k="special_status" />
          </div>
        </div>
      </Card>

      <Collapse title={`补充病史、体征与四诊详情（已填 ${filledExtraCount} 项）`}>
        <div className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="既往史"><textarea className="input min-h-[70px]" disabled={dis} value={draft.past_history} onChange={(x) => set("past_history", x.target.value)} /><Conflict k="past_history" /></Field>
            <Field label="个人史 / 家族史"><textarea className="input min-h-[70px]" disabled={dis} value={draft.personal_family_history} onChange={(x) => set("personal_family_history", x.target.value)} /><Conflict k="personal_family_history" /></Field>
          </div>
          <div className="grid gap-4 lg:grid-cols-[1fr_1.4fr]">
            <div><label className="label">生命体征</label>
              <div className="grid grid-cols-2 gap-2">{VITALS.map(([k, u]) => (
                <div key={k} className="relative"><input className="input pr-12" disabled={dis} placeholder={k} aria-label={k} value={draft.vitals[k] || ""} onChange={(x) => set("vitals", { ...draft.vitals, [k]: x.target.value })} />
                  <span className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-[11px] text-ink-400">{u}</span>
                  <span className="pointer-events-none absolute -top-2 left-2 bg-white px-1 text-[10px] text-ink-400">{k}</span></div>))}</div><Conflict k="vitals" /></div>
            <Field label="体格检查"><textarea className="input min-h-[96px]" disabled={dis} value={draft.physical_exam} onChange={(x) => set("physical_exam", x.target.value)} /><Conflict k="physical_exam" /></Field>
          </div>
          <div>
            <div className="mb-2 flex items-center justify-between"><span className="label !mb-0">中医四诊其余项（医师记录）</span><span className="hint">脉象只能由医师记录；AI 不会臆造舌脉</span></div>
            <div className="grid gap-3 sm:grid-cols-2">{TCM.filter(([k]) => k !== "舌象" && k !== "脉象").map(([k, ph]) => (
              <Field key={k} label={k}><input className="input" disabled={dis} placeholder={ph} value={draft.tcm_four[k] || ""} onChange={(x) => set("tcm_four", { ...draft.tcm_four, [k]: x.target.value })} /></Field>))}</div><Conflict k="tcm_four" />
          </div>
        </div>
      </Collapse>
      {readOnly && <Banner kind="info">本次就诊已结束或您没有编辑权限，临床资料为只读。如需更正已定稿病历，请在“病历”步骤使用“更正”。</Banner>}
    </div>
  );
}

function SaveState({ dirty, saving, savedAt, readOnly, onSave }: { dirty: boolean; saving: boolean; savedAt: string | null; readOnly: boolean; onSave: () => void }) {
  if (readOnly) return null;
  return (
    <div className="flex items-center gap-2 text-xs" aria-live="polite">
      {saving ? <span className="flex items-center gap-1 text-ink-500"><Spinner size={12} />保存中…</span>
        : dirty ? <><span className="text-amber-600">有未保存的修改（将自动保存）</span><button type="button" className="btn-primary btn-sm" onClick={onSave}><Save size={12} />立即保存</button></>
          : savedAt ? <span className="flex items-center gap-1 text-jade-600"><CheckCircle2 size={13} />已保存 {fmtTime(savedAt, false)}</span> : <span className="text-ink-400">修改后自动保存</span>}
    </div>
  );
}
