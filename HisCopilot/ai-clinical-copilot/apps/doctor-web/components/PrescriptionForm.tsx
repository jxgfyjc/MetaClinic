"use client";

import { useState, useEffect, useRef } from "react";
import { apiGet, apiPost, apiPatch } from "@/lib/api";

/**
 * PrescriptionForm — V1.2 §3: editable prescription form.
 *
 * Doctor can:
 *  - search drugs (DB-first via /api/drugs/search)
 *  - add western drugs with editable dose/frequency/usage
 *  - add TCM herbs with gram weight, doses, decoction method
 *  - remove individual drugs
 *  - save → POST /api/prescriptions/drafts (create) or PATCH (update)
 *
 * All drug data comes from real DB/MockHIS — no static JSON faking.
 */

export type DrugEntry = {
  drug_id?: string;
  name: string;
  generic_name?: string;
  category: "western" | "tcm-herb" | "patent";
  form?: string;
  strength?: string;
  unit?: string;
  // editable dosing
  dose?: string;        // e.g. "10mg" or "15g"
  frequency?: string;   // e.g. "qd", "bid", "水煎分两次"
  usage?: string;       // e.g. "口服", "冲服"
  days?: number;
  reason?: string;
  in_hospital_dict?: boolean;
};

export type PrescriptionFormProps = {
  encounterId: string;
  patientId: string;
  doctorId?: string;
  targetDiagnosisId?: string;
  aiRunId?: string;
  /** Initial prescription data from AI (if any) */
  initialPrescription?: any;
  /** Existing draft ID (if editing) */
  draftId?: string;
  onSaved?: (draftId: string) => void;
};

export default function PrescriptionForm({
  encounterId, patientId, doctorId = "dr-001",
  targetDiagnosisId, aiRunId, initialPrescription, draftId: initialDraftId,
  onSaved,
}: PrescriptionFormProps) {
  const [westernMeds, setWesternMeds] = useState<DrugEntry[]>([]);
  const [patentMeds, setPatentMeds] = useState<DrugEntry[]>([]);
  const [herbs, setHerbs] = useState<DrugEntry[]>([]);
  const [formulaName, setFormulaName] = useState("自拟方");
  const [formulaType, setFormulaType] = useState("自拟");
  const [decoction, setDecoction] = useState("水煎");
  const [safetyNotes, setSafetyNotes] = useState<string[]>([]);
  const [draftId, setDraftId] = useState(initialDraftId || "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [status, setStatus] = useState("AI_GENERATED");
  const [sigId, setSigId] = useState("");

  // drug search
  const [searchQ, setSearchQ] = useState("");
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searchCat, setSearchCat] = useState<"all" | "western" | "tcm-herb" | "patent">("all");
  const searchTimer = useRef<NodeJS.Timeout | null>(null);

  // init from AI prescription
  useEffect(() => {
    if (!initialPrescription) return;
    const wm: DrugEntry[] = (initialPrescription.western_medications || []).map((m: any) => ({
      drug_id: m.drug_id, name: m.medication || m.name || "",
      generic_name: m.generic_name, category: "western",
      form: m.form, strength: m.strength, unit: m.unit,
      dose: m.dose, frequency: m.frequency, usage: m.usage || "口服",
      days: m.days, reason: m.reason, in_hospital_dict: m.in_hospital_dict,
    }));
    // §1: patent medicines (中成药) from AI output
    const pm: DrugEntry[] = (initialPrescription.patent_medications || initialPrescription.patent_medicines || []).map((m: any) => ({
      drug_id: m.drug_id, name: m.medication || m.name || "",
      generic_name: m.generic_name, category: "patent",
      form: m.form, strength: m.strength, unit: m.unit,
      dose: m.dose, frequency: m.frequency, usage: m.usage || "口服",
      days: m.days, reason: m.reason, in_hospital_dict: m.in_hospital_dict,
    }));
    const hb: DrugEntry[] = [];
    for (const hd of initialPrescription.herbal_drafts || []) {
      if (hd.name) setFormulaName(hd.name);
      if (hd.formula_type) setFormulaType(hd.formula_type);
      if (hd.decoction) setDecoction(hd.decoction);
      for (const herbName of hd.herbs || []) {
        hb.push({
          name: herbName, category: "tcm-herb",
          dose: hd.doses?.[herbName] || "10g",
        });
      }
    }
    setWesternMeds(wm);
    setPatentMeds(pm);
    setHerbs(hb);
    setSafetyNotes(initialPrescription.safety_notes || []);
  }, [initialPrescription]);

  // §3: load existing draft status when draftId available
  useEffect(() => {
    if (!draftId) return;
    apiGet<{ id: string; status: string }>(`/api/prescriptions/drafts/${draftId}`)
      .then((d) => { if (d.status) setStatus(d.status); })
      .catch(() => {});
  }, [draftId]);

  // debounced drug search
  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!searchQ.trim()) { setSearchResults([]); return; }
    searchTimer.current = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q: searchQ, limit: "20" });
        if (searchCat !== "all") params.set("category", searchCat);
        const r = await apiGet<{ drugs: any[] }>(`/api/drugs/search?${params}`);
        setSearchResults(r.drugs || []);
      } catch { setSearchResults([]); }
    }, 300);
  }, [searchQ, searchCat]);

  function addDrug(d: any) {
    const entry: DrugEntry = {
      drug_id: d.id, name: d.name, generic_name: d.generic_name,
      category: d.category || "western", form: d.form,
      strength: d.strength, unit: d.unit, usage: d.usage || "口服",
      dose: d.strength || "", frequency: "qd", days: 7,
      in_hospital_dict: d.hospital_dict ?? true,
    };
    if (entry.category === "tcm-herb") {
      setHerbs([...herbs, { ...entry, dose: "10g", usage: undefined }]);
    } else if (entry.category === "patent") {
      setPatentMeds([...patentMeds, entry]);
    } else {
      setWesternMeds([...westernMeds, entry]);
    }
    setSearchQ(""); setSearchResults([]);
  }

  function removeWestern(i: number) {
    setWesternMeds(westernMeds.filter((_, idx) => idx !== i));
  }
  function removePatent(i: number) {
    setPatentMeds(patentMeds.filter((_, idx) => idx !== i));
  }
  function removeHerb(i: number) {
    setHerbs(herbs.filter((_, idx) => idx !== i));
  }
  function updateWestern(i: number, patch: Partial<DrugEntry>) {
    setWesternMeds(westernMeds.map((w, idx) => idx === i ? { ...w, ...patch } : w));
  }
  function updatePatent(i: number, patch: Partial<DrugEntry>) {
    setPatentMeds(patentMeds.map((p, idx) => idx === i ? { ...p, ...patch } : p));
  }
  function updateHerb(i: number, patch: Partial<DrugEntry>) {
    setHerbs(herbs.map((h, idx) => idx === i ? { ...h, ...patch } : h));
  }

  async function save() {
    setBusy(true); setMsg("");
    try {
      const prescription = {
        western_medications: westernMeds.map(w => ({
          medication: w.name, drug_id: w.drug_id,
          generic_name: w.generic_name, dose: w.dose,
          frequency: w.frequency, usage: w.usage, days: w.days,
          reason: w.reason, in_hospital_dict: w.in_hospital_dict,
        })),
        patent_medications: patentMeds.map(p => ({
          medication: p.name, drug_id: p.drug_id,
          generic_name: p.generic_name, dose: p.dose,
          frequency: p.frequency, usage: p.usage, days: p.days,
          reason: p.reason, in_hospital_dict: p.in_hospital_dict,
        })),
        herbal_drafts: herbs.length > 0 ? [{
          name: formulaName || "自拟方", formula_type: formulaType || "自拟",
          herbs: herbs.map(h => h.name),
          doses: Object.fromEntries(herbs.map(h => [h.name, h.dose || "10g"])),
          decoction: decoction || "水煎",
        }] : [],
        safety_notes: safetyNotes,
      };
      let resultId = draftId;
      if (draftId) {
        await apiPatch(`/api/prescriptions/drafts/${draftId}`, { prescription });
        setMsg("✅ 处方已更新");
      } else {
        const r = await apiPost<{ id: string }>("/api/prescriptions/drafts", {
          encounter_id: encounterId, patient_id: patientId,
          doctor_id: doctorId, ai_run_id: aiRunId,
          target_diagnosis_id: targetDiagnosisId,
          prescription,
        });
        resultId = r.id; setDraftId(r.id);
        setMsg("✅ 处方草稿已保存");
      }
      onSaved?.(resultId || "");
    } catch (e: any) {
      setMsg(`❌ 保存失败: ${e.message}`);
    } finally { setBusy(false); }
  }

  // §4: confirm → sign → submit HIS (server-controlled status machine)
  async function confirmDraft() {
    if (!draftId) return;
    setBusy(true); setMsg("");
    try {
      await apiPost(`/api/prescriptions/drafts/${draftId}/confirm`, {});
      setStatus("DOCTOR_CONFIRMED"); setMsg("✅ 已确认");
    } catch (e: any) { setMsg(`❌ ${e.message}`); } finally { setBusy(false); }
  }

  async function sign() {
    if (!draftId) return;
    setBusy(true); setMsg("");
    try {
      const r = await apiPost<{ signature_id: string }>(
        `/api/prescriptions/drafts/${draftId}/sign`, {});
      setStatus("SIGNED"); setSigId(r.signature_id || "");
      setMsg(`✅ 已签字`);
    } catch (e: any) { setMsg(`❌ ${e.message}`); } finally { setBusy(false); }
  }

  async function submitHIS() {
    if (!draftId) return;
    setBusy(true); setMsg("");
    try {
      const prescription = {
        western_medications: westernMeds.map(w => ({
          medication: w.name, drug_id: w.drug_id, dose: w.dose,
          frequency: w.frequency, usage: w.usage,
        })),
        herbal_drafts: herbs.length > 0 ? [{
          name: formulaName, herbs: herbs.map(h => h.name),
          doses: Object.fromEntries(herbs.map(h => [h.name, h.dose || "10g"])),
        }] : [],
      };
      const r = await apiPost(`/api/prescriptions/${draftId}/submit-his`, {
        encounter_id: encounterId, doctor_id: doctorId,
        prescription, signature_id: sigId,
      });
      setStatus("SUBMITTED_TO_HIS");
      setMsg(`✅ 已提交 HIS（${r.his?.his_status || "ok"}）`);
    } catch (e: any) { setMsg(`❌ ${e.message}`); } finally { setBusy(false); }
  }

  return (
    <div className="card border-l-4 border-l-brand-500 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold text-brand-700">处方编辑（医生可增删改药物）</h3>
        {targetDiagnosisId && (
          <span className="text-xs text-brand-600">针对诊断：{targetDiagnosisId}</span>
        )}
      </div>

      {/* Drug search */}
      <div className="border rounded p-2 bg-slate-50">
        <div className="flex gap-2 mb-1">
          <input
            value={searchQ}
            onChange={e => setSearchQ(e.target.value)}
            className="flex-1 border rounded px-2 py-1 text-sm"
            placeholder="搜索药品/饮片名称或通用名…"
          />
          <select value={searchCat} onChange={e => setSearchCat(e.target.value as any)}
            className="border rounded px-2 py-1 text-xs">
            <option value="all">全部</option>
            <option value="western">西药</option>
            <option value="patent">中成药</option>
            <option value="tcm-herb">中药饮片</option>
          </select>
        </div>
        {searchResults.length > 0 && (
          <div className="max-h-40 overflow-y-auto bg-white border rounded text-xs">
            {searchResults.map((d, i) => (
              <button key={i} onClick={() => addDrug(d)}
                className="w-full text-left px-2 py-1 hover:bg-brand-50 border-b last:border-0">
                <span className="font-medium">{d.name}</span>
                {d.generic_name && <span className="text-slate-500"> ({d.generic_name})</span>}
                <span className="text-slate-400 ml-2">
                  {d.form || ""} {d.strength || ""} {d.unit || ""}
                </span>
                <span className="badge-muted ml-2">{d.category === "tcm-herb" ? "中药" : "西药"}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Western meds */}
      {westernMeds.length > 0 && (
        <div>
          <h4 className="text-sm font-medium mb-1">西药 / 成药</h4>
          <div className="space-y-1">
            {westernMeds.map((m, i) => (
              <div key={i} className="flex flex-wrap items-center gap-1 text-xs border rounded p-1.5">
                <span className="font-medium min-w-[80px]">{m.name}</span>
                <input value={m.dose || ""} onChange={e => updateWestern(i, { dose: e.target.value })}
                  className="border rounded px-1 w-16" placeholder="剂量" />
                <input value={m.frequency || ""} onChange={e => updateWestern(i, { frequency: e.target.value })}
                  className="border rounded px-1 w-16" placeholder="频次" />
                <input value={m.usage || ""} onChange={e => updateWestern(i, { usage: e.target.value })}
                  className="border rounded px-1 w-16" placeholder="用法" />
                <input type="number" value={m.days || ""} onChange={e => updateWestern(i, { days: parseInt(e.target.value) || undefined })}
                  className="border rounded px-1 w-14" placeholder="天" />
                <input value={m.reason || ""} onChange={e => updateWestern(i, { reason: e.target.value })}
                  className="border rounded px-1 flex-1 min-w-[100px]" placeholder="用途" />
                {m.in_hospital_dict === false && <span className="badge-warn">院内未配置</span>}
                <button onClick={() => removeWestern(i)} className="text-red-500 hover:text-red-700">✕</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Patent medicines (中成药) */}
      {patentMeds.length > 0 && (
        <div>
          <h4 className="text-sm font-medium mb-1">中成药</h4>
          <div className="space-y-1">
            {patentMeds.map((m, i) => (
              <div key={i} className="flex flex-wrap items-center gap-1 text-xs border rounded p-1.5">
                <span className="font-medium min-w-[80px]">{m.name}</span>
                <input value={m.dose || ""} onChange={e => updatePatent(i, { dose: e.target.value })}
                  className="border rounded px-1 w-16" placeholder="剂量" />
                <input value={m.frequency || ""} onChange={e => updatePatent(i, { frequency: e.target.value })}
                  className="border rounded px-1 w-16" placeholder="频次" />
                <input value={m.usage || ""} onChange={e => updatePatent(i, { usage: e.target.value })}
                  className="border rounded px-1 w-16" placeholder="用法" />
                <input type="number" value={m.days || ""} onChange={e => updatePatent(i, { days: parseInt(e.target.value) || undefined })}
                  className="border rounded px-1 w-14" placeholder="天" />
                <input value={m.reason || ""} onChange={e => updatePatent(i, { reason: e.target.value })}
                  className="border rounded px-1 flex-1 min-w-[100px]" placeholder="用途" />
                {m.in_hospital_dict === false && <span className="badge-warn">院内未配置</span>}
                <button onClick={() => removePatent(i)} className="text-red-500 hover:text-red-700">✕</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Herbs */}
      {herbs.length > 0 && (
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h4 className="text-sm font-medium">中药饮片</h4>
            <input value={formulaName} onChange={e => setFormulaName(e.target.value)}
              className="border rounded px-2 py-0.5 text-xs w-28" placeholder="方名" />
            <select value={formulaType} onChange={e => setFormulaType(e.target.value)}
              className="border rounded px-1 py-0.5 text-xs">
              <option value="自拟">自拟</option>
              <option value="协定方">协定方</option>
              <option value="经方">经方</option>
              <option value="验方">验方</option>
            </select>
            <input value={decoction} onChange={e => setDecoction(e.target.value)}
              className="border rounded px-2 py-0.5 text-xs w-20" placeholder="煎法" />
          </div>
          <div className="space-y-1">
            {herbs.map((h, i) => (
              <div key={i} className="flex flex-wrap items-center gap-1 text-xs border rounded p-1.5">
                <span className="font-medium min-w-[60px]">{h.name}</span>
                <input value={h.dose || ""} onChange={e => updateHerb(i, { dose: e.target.value })}
                  className="border rounded px-1 w-20" placeholder="克数" />
                <button onClick={() => removeHerb(i)} className="text-red-500 hover:text-red-700">✕</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Safety notes */}
      {safetyNotes.length > 0 && (
        <div className="text-xs text-red-700">
          <b>安全提示：</b>
          <ul className="list-disc pl-5">
            {safetyNotes.map((s, i) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}

      {westernMeds.length === 0 && patentMeds.length === 0 && herbs.length === 0 && (
        <div className="text-xs text-slate-400">暂无药物，请在上方搜索添加。</div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        <button onClick={save} disabled={busy || (westernMeds.length === 0 && patentMeds.length === 0 && herbs.length === 0)}
          className="btn-primary text-xs">
          {busy ? "保存中…" : "保存处方草稿"}
        </button>
        {draftId && status === "AI_GENERATED" && (
          <button onClick={confirmDraft} disabled={busy} className="btn-ghost text-xs">确认草稿</button>
        )}
        {draftId && status === "DOCTOR_CONFIRMED" && (
          <button onClick={sign} disabled={busy} className="btn-ghost text-xs">医生签字</button>
        )}
        {draftId && status === "SIGNED" && (
          <button onClick={submitHIS} disabled={busy} className="btn-primary text-xs">提交 HIS</button>
        )}
        {draftId && <span className="badge-muted text-xs">{status}</span>}
        {msg && <span className="text-xs text-slate-500">{msg}</span>}
        {draftId && <span className="text-xs text-slate-400">草稿ID: {draftId}</span>}
      </div>
      <div className="text-xs text-slate-400">
        V1.2 §3：药品库支持搜索/新增/编辑/删除/CSV导入；此处修改直接持久化到数据库。
      </div>
    </div>
  );
}
