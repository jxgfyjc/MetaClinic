"use client";

import { useState, useEffect, useRef } from "react";
import {
  apiPost, apiPatch, apiGet, pollAiRun, panelBadge, API_BASE,
  type AiRun, type Encounter, type Patient, type DoctorProfile,
} from "@/lib/api";
import PrescriptionForm from "@/components/PrescriptionForm";

// demo.md 第五节 + 第六节: 工作台最小改造. 白/浅灰/医疗蓝, 三层信息区分:
// 患者事实(中性) / AI建议(蓝标记) / 医生编辑文书(边框+签字态). 模块状态由真实模型
// 调用驱动 (pollAiRun), 无假进度.

export default function AnalyzePanel({
  eid, encounter, patient,
}: { eid: string; encounter: Encounter | null; patient: Patient | null }) {
  const [loading, setLoading] = useState(false);
  const [run, setRun] = useState<AiRun | null>(null);
  const [error, setError] = useState("");
  const [moduleStatus, setModuleStatus] = useState<Record<string, string>>({});

  // instruct_1.md §3: doctor selector for multi-doctor personalization.
  // Default dr-001; user can switch to compare styles on the same case.
  const [doctorId, setDoctorId] = useState("dr-001");
  const [doctors, setDoctors] = useState<{id:string;name:string}[]>([]);
  const [profile, setProfile] = useState<DoctorProfile | null>(null);

  useEffect(() => {
    apiGet<{id:string;name:string}[]>("/api/doctors").then(setDoctors).catch(() => {});
  }, []);
  useEffect(() => {
    apiGet<DoctorProfile>(`/api/doctors/${doctorId}/profile`).then(setProfile).catch(() => {});
  }, [doctorId]);

  // §3: load latest persisted AI run on mount — don't re-run if analysis exists
  const [existingRunTs, setExistingRunTs] = useState("");
  useEffect(() => {
    apiGet<{ run_id: string | null; status: string } & AiRun>(
      `/api/encounters/${eid}/ai/latest`)
      .then((res) => {
        if (res.run_id && res.status && res.status !== "none" &&
            ["SUCCEEDED", "PARTIAL"].includes(res.status)) {
          setRun(res as AiRun);
          setModuleStatus(res.panel_status || {});
          setExistingRunTs(res.latency_ms ? `${res.latency_ms}ms` : "");
        }
      })
      .catch(() => {});
  }, [eid]);

  // 病历编辑器
  const [mrText, setMrText] = useState("");
  const [mrSaved, setMrSaved] = useState("");

  // V1.2 §2: diagnosis records
  const [diagnoses, setDiagnoses] = useState<any[]>([]);
  const [prescriptionDrafts, setPrescriptionDrafts] = useState<Record<string, string>>({});

  async function refreshDiagnoses() {
    try {
      const d = await apiGet<any[]>(`/api/encounters/${eid}/diagnoses`);
      setDiagnoses(d);
    } catch {}
  }
  useEffect(() => { refreshDiagnoses(); }, [eid]);

  // updatev2_3 §4: real-time sync — when confirmed diagnoses OR the unified
  // treatment draft change, update the mrText editor's marked blocks WITHOUT
  // overwriting the doctor's manual content outside those blocks.
  // - 【已确认诊断】 block ← confirmed diagnoses
  // - 【综合治疗方案】 block ← unified treatment draft (prescription + therapy)
  // Each block is replaced in place; text outside is preserved.
  const mrDiagBlockRef = useRef<string>("");
  const mrTxBlockRef = useRef<string>("");
  const [unifiedDraft, setUnifiedDraft] = useState<any>(null);

  function _replaceBlock(prev: string, open: string, close: string, block: string): string {
    const start = prev.indexOf(open);
    if (start === -1) {
      const sep = prev && !prev.endsWith("\n") ? "\n\n" : (prev ? "\n" : "");
      return prev + sep + block;
    }
    const end = prev.indexOf(close);
    if (end === -1) {
      return prev + "\n" + close + "\n\n" + block;
    }
    return prev.slice(0, start) + block + prev.slice(end + close.length);
  }

  // Diagnoses block
  useEffect(() => {
    const OPEN = "【已确认诊断】";
    const CLOSE = "【/已确认诊断】";
    const confirmed = diagnoses.filter(
      (d) => d.review_status === "accepted" || d.review_status === "modified");
    if (confirmed.length === 0) {
      if (mrDiagBlockRef.current && mrText.includes(OPEN)) {
        const start = mrText.indexOf(OPEN);
        const end = mrText.indexOf(CLOSE);
        if (start !== -1 && end !== -1) {
          let removed = mrText.slice(0, start) + mrText.slice(end + CLOSE.length);
          removed = removed.replace(/\n{3,}/g, "\n\n").replace(/^\n+/, "");
          setMrText(removed);
        }
      }
      mrDiagBlockRef.current = "";
      return;
    }
    const lines = confirmed.map((d, i) => {
      const wd = d.western_diagnosis || d.western || "";
      const ts = d.tcm_syndrome || d.tcm || "";
      const ev = d.evidence || d.main_evidence || d.reason || "";
      const parts: string[] = [];
      if (wd) parts.push(`西医：${wd}`);
      if (ts) parts.push(`中医：${ts}`);
      if (ev) parts.push(`主要依据：${ev}`);
      return `${i + 1}. ${parts.join("（")}${parts.length > 1 ? "）" : ""}`;
    });
    const block = `${OPEN}\n${lines.join("\n")}\n${CLOSE}`;
    if (block === mrDiagBlockRef.current && mrText.includes(OPEN)) return;
    mrDiagBlockRef.current = block;
    setMrText((prev) => _replaceBlock(prev, OPEN, CLOSE, block));
  }, [diagnoses]);

  // Treatment plan block — fires when the unified treatment draft loads/changes.
  // Writes a human-readable prescription + therapy summary into 【综合治疗方案】.
  useEffect(() => {
    const OPEN = "【综合治疗方案】";
    const CLOSE = "【/综合治疗方案】";
    if (!unifiedDraft || (!unifiedDraft.prescription && !unifiedDraft.therapy)) {
      // remove block if present
      if (mrTxBlockRef.current && mrText.includes(OPEN)) {
        const start = mrText.indexOf(OPEN);
        const end = mrText.indexOf(CLOSE);
        if (start !== -1 && end !== -1) {
          let removed = mrText.slice(0, start) + mrText.slice(end + CLOSE.length);
          removed = removed.replace(/\n{3,}/g, "\n\n").replace(/^\n+/, "");
          setMrText(removed);
        }
      }
      mrTxBlockRef.current = "";
      return;
    }
    const lines: string[] = [];
    const presc = unifiedDraft.prescription || {};
    const wm = presc.western_medications || [];
    if (wm.length) {
      lines.push("【西药】");
      wm.forEach((m: any, i: number) => {
        lines.push(`  ${i + 1}. ${m.medication || m.name || ""} ${m.dose || ""} ${m.frequency || ""} ${m.duration || ""}${m.reason ? "（" + m.reason + "）" : ""}`);
      });
    }
    const pm = presc.patent_medications || presc.patent_medicines || [];
    if (pm.length) {
      lines.push("【中成药】");
      pm.forEach((m: any, i: number) => {
        lines.push(`  ${i + 1}. ${m.medication || m.name || ""} ${m.dose || ""} ${m.frequency || ""}${m.reason ? "（" + m.reason + "）" : ""}`);
      });
    }
    const hds = presc.herbal_drafts || [];
    if (hds.length) {
      lines.push("【中药饮片】");
      hds.forEach((hd: any) => {
        const herbs = hd.herbs || [];
        const doses = hd.doses || {};
        const parts = herbs.map((h: string) => `${h} ${doses[h] || ""}`);
        lines.push(`  ${hd.name || "自拟方"}（${hd.formula_type || ""}）${hd.decoction || "水煎"}：${parts.join("、")}`);
        if (hd.rationale) lines.push(`  组方思路：${hd.rationale}`);
      });
    }
    if (presc.safety_notes && presc.safety_notes.length) {
      lines.push("【用药提示】" + presc.safety_notes.filter(Boolean).join("；"));
    }
    const therapy = unifiedDraft.therapy || {};
    const cands = therapy.candidates || [];
    if (cands.length) {
      lines.push("【理疗方案】");
      cands.forEach((c: any, i: number) => {
        const ap = (c.acupoints || []).join("、");
        lines.push(`  ${i + 1}. ${c.category || ""} ${c.name || ""}${ap ? " 穴位：" + ap : ""}${c.manipulation ? " 手法：" + c.manipulation : ""}${c.direction_or_stimulation ? " " + c.direction_or_stimulation : ""}${c.duration_or_count ? " " + c.duration_or_count : ""}${c.frequency ? " " + c.frequency : ""}${c.course ? " " + c.course : ""}`);
        if (c.location_basis) lines.push(`  定位：${c.location_basis}`);
        if (c.therapeutic_goal) lines.push(`  目标：${c.therapeutic_goal}`);
      });
    }
    if (therapy.lifestyle_advice && therapy.lifestyle_advice.length) {
      lines.push("【生活建议】" + therapy.lifestyle_advice.filter(Boolean).join("；"));
    }
    if (unifiedDraft.unified_plan_summary) {
      lines.unshift("方案总述：" + unifiedDraft.unified_plan_summary);
    }
    const block = `${OPEN}\n${lines.join("\n")}\n${CLOSE}`;
    if (block === mrTxBlockRef.current && mrText.includes(OPEN)) return;
    mrTxBlockRef.current = block;
    setMrText((prev) => _replaceBlock(prev, OPEN, CLOSE, block));
  }, [unifiedDraft]);

  // V1.0: 预填自动病历草稿（encounter.medical_record.content.text，由后端模板生成）
  // 医生在此基础上编辑，不覆盖已保存的人工版本
  useEffect(() => {
    const draft = encounter?.medical_record?.content?.text
      || encounter?.medical_record?.raw_text
      || encounter?.medical_record?.text;
    if (draft) setMrText(draft);
  }, [encounter?.medical_record]);

  // 自定义病情输入 (instruct_1.md §5: 工作台可自定义输入)
  // 预填就诊数据，但医生可任意修改后再启动 AI 分析
  const [customMode, setCustomMode] = useState(false);
  const [cChief, setCChief] = useState(encounter?.chief_complaint || "");
  const [cRawText, setCRawText] = useState(encounter?.raw_text || "");
  const [cSymptoms, setCSymptoms] = useState(
    (encounter?.history?.symptoms || []).join("、"));
  const [cComorbidities, setComorbidities] = useState(
    (encounter?.history?.comorbidities || []).join("、"));
  const [cAllergies, setCAllergies] = useState(
    (encounter?.allergies || patient?.allergies || [])
      .map((a:any) => a.allergen).join("、"));
  const [cMeds, setCMeds] = useState(
    (encounter?.medications || [])
      .map((m:any) => `${m.name} ${m.dose||""} ${m.freq||""}`).join("；"));
  const [cLabs, setCLabs] = useState(
    (encounter?.labs || [])
      .map((l:any) => `${l.item}=${l.value}${l.unit||""}(${l.ref||""})${l.flag?" "+l.flag:""}`)
      .join("；"));

  // 舌象图片上传 (instruct #14, instruct_1.md §5)
  const [tongueKey, setTongueKey] = useState("");
  const [tonguePreview, setTonguePreview] = useState("");
  const [tongueUploading, setTongueUploading] = useState(false);
  const [tongueMsg, setTongueMsg] = useState("");

  async function uploadTongue(file: File) {
    setTongueUploading(true); setTongueMsg("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${API_BASE}/api/encounters/${eid}/tongue-image`, {
        method: "POST", body: fd,
      });
      if (!r.ok) {
        const t = await r.text();
        setTongueMsg(`上传失败: ${r.status} ${t}`);
      } else {
        const d = await r.json();
        setTongueKey(d.key); // V1.2: compressed key for model
        setTonguePreview(`${API_BASE}${d.url}`);
        setTongueMsg("舌象已上传（压缩副本已生成），启动 AI 分析后舌诊模块将使用压缩图真实运行");
      }
    } catch (e: any) {
      setTongueMsg(`上传失败: ${e.message}`);
    } finally { setTongueUploading(false); }
  }

  async function runAnalysis() {
    setLoading(true); setError(""); setRun(null);
    setModuleStatus({
      western: "pending", tcm: "pending", tongue: "pending",
      knowledge: "pending", synthesis: "pending", prescription: "pending",
      therapy: "pending",
    });
    // 自定义模式：解析用户输入的字符串为结构化数据
    const encHistory = customMode ? {
      symptoms: cSymptoms ? cSymptoms.split(/[、,，;；\s]+/).filter(Boolean) : [],
      comorbidities: cComorbidities ? cComorbidities.split(/[、,，;；\s]+/).filter(Boolean) : [],
    } : (encounter?.history || {});
    const encAllergies = customMode ? (
      cAllergies ? cAllergies.split(/[、,，;；\s]+/).filter(Boolean)
        .map((a:string) => ({allergen:a, severity:"unknown"})) : []
    ) : (encounter?.allergies || patient?.allergies || []);
    const encMeds = customMode ? (
      cMeds ? cMeds.split(/[；;,，\n]+/).filter(Boolean).map((s:string) => {
        const parts = s.trim().split(/\s+/);
        return { name: parts[0]||s, dose: parts[1]||"", freq: parts[2]||"" };
      }) : []
    ) : (encounter?.medications || []);
    const encLabs = customMode ? (
      cLabs ? cLabs.split(/[；;,，\n]+/).filter(Boolean).map((s:string) => {
        // parse "HbA1c=8.2%(<7) HIGH"
        const m = s.match(/^(\S+?)=(\d+\.?\d*)(\D*?)\(([^)]+)\)\s*(\w*)/);
        if (m) return {item:m[1], value:m[2], unit:m[3]||"", ref:m[4], flag:m[5]||""};
        return {item:s, value:"", unit:"", ref:"", flag:""};
      }) : []
    ) : (encounter?.labs || []);
    const encRawText = customMode ? cRawText : (encounter?.raw_text || "");
    const encChief = customMode ? cChief : (encounter?.chief_complaint || "");

    try {
      const start = await apiPost<{ ai_run_id: string }>(
        `/api/encounters/${eid}/ai/analyze`,
        {
          encounter_id: eid,
          patient_id: encounter?.patient_id || patient?.id || "",
          patient: patient || { id: encounter?.patient_id },
          medical_history: { ...encHistory,
            chief_complaint: encChief, patient_raw_text: encRawText },
          labs: encLabs,
          medications: encMeds,
          allergies: encAllergies,
          tongue_features: {},
          tongue_image_key: tongueKey,
          raw_text: encRawText,
          doctor_id: doctorId,
        });
      // poll for real module status (demo.md 第六节)
      const final = await pollAiRun(start.ai_run_id, (r) => {
        setRun(r);
        setModuleStatus(r.panel_status || {});
      });
      setRun(final);
      setModuleStatus(final.panel_status || {});
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function saveMedicalRecord() {
    setMrSaved("");
    try {
      const r = await apiPost(`/api/encounters/${eid}/medical-record`, {
        patient_id: encounter?.patient_id || patient?.id || "",
        doctor_id: encounter?.doctor_id || "dr-001",
        content: { text: mrText, encounter_id: eid },
        raw_text: mrText,
      });
      setMrSaved(r.updated ? "已保存（新版本）" : "内容未变，未覆盖");
    } catch (e: any) { setMrSaved(`保存失败: ${e.message}`); }
  }

  // updatev2_3 §2g: finalize — assemble final record from confirmed
  // diagnoses + unified treatment plan + e-prescriptions + therapy reviews.
  // The finalized version is a recoverable historical record; later AI re-runs
  // never silently overwrite it.
  const [finalizedMsg, setFinalizedMsg] = useState("");
  async function finalizeRecord() {
    setFinalizedMsg("");
    try {
      const r = await apiPost<{ id: string; status: string }>(
        `/api/encounters/${eid}/finalize-record`, { signature_id: "" });
      setFinalizedMsg(`✅ 已定稿，记录号 ${r.id}`);
    } catch (e: any) {
      setFinalizedMsg(`定稿失败: ${e.message}`);
    }
  }

  const allergies = encounter?.allergies || patient?.allergies || [];
  const labs = encounter?.labs || [];
  const meds = encounter?.medications || [];
  const history = encounter?.history || {};

  return (
    <div className="space-y-4">
      {/* 诊疗闭环引导 */}
      <div className="card bg-slate-50 text-xs text-slate-600 p-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium">诊疗闭环：</span>
          <span className={run ? "text-brand-600 font-medium" : ""}>① 采集病情</span>→
          <span className={run ? "text-brand-600 font-medium" : ""}>② AI 分析</span>→
          <span className={diagnoses.some(d => d.review_status === "accepted") ? "text-brand-600 font-medium" : ""}>③ 确认诊断</span>→
          <span className="text-slate-400">④ 生成治疗方案</span>→
          <span className="text-slate-400">⑤ 编辑处方</span>→
          <span className="text-slate-400">⑥ 签字提交</span>→
          <span className="text-slate-400">⑦ 随访</span>
        </div>
      </div>
      {/* === 患者事实层 (中性) === */}
      <section className="card border-l-4 border-l-slate-400">
        <div className="flex items-center justify-between mb-2">
          <h2 className="font-semibold text-slate-700">患者信息与就诊事实</h2>
          {/* 自定义病情输入开关 (instruct_1.md §5) */}
          <label className="flex items-center gap-1 text-xs text-slate-600 cursor-pointer">
            <input
              type="checkbox"
              checked={customMode}
              onChange={(e) => setCustomMode(e.target.checked)}
              className="accent-brand-500"
            />
            自定义病情输入
          </label>
        </div>
        {customMode ? (
          /* 自定义模式：医生可任意输入/修改病情 */
          <div className="space-y-2 text-sm">
            <div>
              <label className="text-xs text-slate-500">主诉</label>
              <input value={cChief} onChange={(e)=>setCChief(e.target.value)}
                className="w-full border rounded px-2 py-1 text-sm"
                placeholder="如：咳嗽1周，发热3天" />
            </div>
            <div>
              <label className="text-xs text-slate-500">现病史（自由文本，传给 AI）</label>
              <textarea value={cRawText} onChange={(e)=>setCRawText(e.target.value)}
                className="w-full border rounded px-2 py-1 text-sm min-h-[60px]"
                placeholder="如：咳嗽1周，黄痰，发热3天最高38.5℃，口渴乏力，舌红苔黄" />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-slate-500">症状（顿号分隔）</label>
                <input value={cSymptoms} onChange={(e)=>setCSymptoms(e.target.value)}
                  className="w-full border rounded px-2 py-1 text-sm"
                  placeholder="咳嗽、黄痰、发热" />
              </div>
              <div>
                <label className="text-xs text-slate-500">慢病（顿号分隔）</label>
                <input value={cComorbidities} onChange={(e)=>setComorbidities(e.target.value)}
                  className="w-full border rounded px-2 py-1 text-sm"
                  placeholder="高血压、2型糖尿病" />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-slate-500">过敏史（顿号分隔）</label>
                <input value={cAllergies} onChange={(e)=>setCAllergies(e.target.value)}
                  className="w-full border rounded px-2 py-1 text-sm"
                  placeholder="青霉素、海鲜" />
              </div>
              <div>
                <label className="text-xs text-slate-500">当前用药（分号分隔：药名 剂量 频次）</label>
                <input value={cMeds} onChange={(e)=>setCMeds(e.target.value)}
                  className="w-full border rounded px-2 py-1 text-sm"
                  placeholder="二甲双胍 0.5g bid；氨氯地平 5mg qd" />
              </div>
            </div>
            <div>
              <label className="text-xs text-slate-500">检验（分号分隔：项目=数值单位(参考范围) 标记）</label>
              <input value={cLabs} onChange={(e)=>setCLabs(e.target.value)}
                className="w-full border rounded px-2 py-1 text-sm"
                placeholder="HbA1c=8.2%(<7) HIGH；FBG=9.1mmol/L(3.9-6.1) HIGH" />
            </div>
            <div className="text-xs text-brand-600 mt-1">
              勾选后上方输入框将覆盖该就诊的预设数据，启动 AI 分析时使用你输入的病情。
            </div>
          </div>
        ) : (
          /* 默认模式：展示就诊预设数据 */
          <>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-sm">
            <div><b>姓名：</b>{patient?.name || "未填写"}</div>
            <div><b>性别：</b>{patient?.gender || "未填写"}</div>
            <div><b>年龄：</b>{patient?.age ? `${patient.age}岁` : "未填写"}</div>
            <div><b>主诉：</b>{encounter?.chief_complaint || "未填写"}</div>
            <div className="md:col-span-2"><b>现病史：</b>{encounter?.raw_text || "未填写"}</div>
          </div>
          {allergies.length > 0 && (
            <div className="mt-2 text-sm">
              <b>过敏史：</b>
              {allergies.map((a: any, i: number) => (
                <span key={i} className="badge-err mr-1">{a.allergen}/{a.severity}</span>
              ))}
            </div>
          )}
          {history.symptoms?.length > 0 && (
            <div className="mt-2 text-sm"><b>症状：</b>{history.symptoms.join("、")}</div>
          )}
          {history.comorbidities?.length > 0 && (
            <div className="mt-2 text-sm"><b>慢病：</b>{history.comorbidities.join("、")}</div>
          )}
          {meds.length > 0 && (
            <div className="mt-2 text-sm">
              <b>当前用药：</b>
              {meds.map((m: any, i: number) => (
                <span key={i} className="badge-muted mr-1">{m.name} {m.dose} {m.freq}</span>
              ))}
            </div>
          )}
          {labs.length > 0 && (
            <div className="mt-2 text-sm">
              <b>检验：</b>
              {labs.map((l: any, i: number) => (
                <span key={i} className={l.flag === "HIGH" ? "badge-warn mr-1" : l.flag === "NORMAL" ? "badge-ok mr-1" : "badge-muted mr-1"}>
                  {l.item} {l.value}{l.unit} ({l.ref})
                </span>
              ))}
            </div>
          )}
          </>
        )}
        {/* 舌象图片上传 (instruct #14, instruct_1.md §5) */}
        <div className="mt-3 border-t pt-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">舌象图片：</span>
            <label className="btn-ghost text-xs cursor-pointer">
              {tongueUploading ? "上传中…" : "选择图片上传"}
              <input
                type="file"
                accept="image/png,image/jpeg,image/webp"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) uploadTongue(f);
                }}
              />
            </label>
            {tongueKey && (
              <span className="badge-ok text-xs">已上传 ✓</span>
            )}
            {tongueMsg && <span className="text-xs text-slate-500">{tongueMsg}</span>}
          </div>
          {tonguePreview && (
            <div className="mt-2">
              <img src={tonguePreview} alt="舌象" className="max-h-40 rounded border border-slate-300" />
            </div>
          )}
          {!tongueKey && (
            <div className="text-xs text-slate-400 mt-1">
              未上传舌象图时，舌诊模块返回 UNAVAILABLE（不影响其他模块运行）。
              上传后启动 AI 分析，舌诊将调用 clinical_main VLM 真实分析图片。
            </div>
          )}
        </div>
      </section>

      {/* === AI 副驾层 (蓝标记) === */}
      <section className="card border-l-4 border-l-brand-500">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <h2 className="font-semibold text-brand-700">AI 副驾分析</h2>
          <div className="flex items-center gap-2">
            {/* §3: if existing run loaded, show "已有分析" notice */}
            {run && existingRunTs && !loading && (
              <span className="text-xs text-slate-500">
                已有分析结果（不重新运行），如需更新请点击重新分析
              </span>
            )}
            {/* instruct_1.md §3: doctor selector for personalization comparison */}
            <select
              value={doctorId}
              onChange={(e) => setDoctorId(e.target.value)}
              disabled={loading}
              className="border rounded text-xs px-2 py-1"
            >
              {doctors.length > 0 ? doctors.map((d) => (
                <option key={d.id} value={d.id}>{d.name}</option>
              )) : (
                <>
                  <option value="dr-001">dr-001 张明</option>
                  <option value="dr-002">dr-002 李秀英</option>
                </>
              )}
            </select>
            <button onClick={runAnalysis} disabled={loading} className="btn-primary">
              {loading ? "分析中…" : run ? "重新分析" : "启动 AI 分析"}
            </button>
          </div>
        </div>
        {/* doctor profile summary (instruct_1.md §3) */}
        {profile && (
          <div className="text-xs text-slate-600 mb-2 border-l-2 border-brand-200 pl-2">
            <b>当前医生画像：</b>{profile.dept} · {profile.specialties.join("、")}<br/>
            <span className="text-slate-500">辨证偏好：</span>{profile.syndrome_preference}<br/>
            <span className="text-slate-500">组方偏好：</span>{profile.herb_preference}
          </div>
        )}
        {/* 模块状态条 — 真实模型调用驱动, V1.2 §7: per-module timing */}
        <div className="flex flex-wrap gap-2 text-xs mb-2">
          {Object.entries(moduleStatus).map(([k, v]) => {
            const timing = run?.module_timings?.[k];
            return (
              <span key={k} className={panelBadge(v)}>
                {_moduleLabel(k)}: {_statusLabel(v)}
                {timing && typeof timing.duration_ms === "number" && v === "complete" && (
                  <span className="ml-1 opacity-70">
                    {(timing.duration_ms / 1000).toFixed(1)}s
                  </span>
                )}
              </span>
            );
          })}
        </div>
        {loading && !run?.synthesis && (
          <div className="text-xs text-slate-500">真实模型推理中，模块状态随实际完成更新（非假进度）…</div>
        )}
        {error && (
          <div className="text-sm text-red-700 border border-red-300 bg-red-50 p-2 rounded">
            AI 不可用：{error}（instruct #30：AI 失效降级，HIS 主流程不阻塞）
          </div>
        )}
      </section>

      {run && (
        <div className="space-y-4">
          {/* run status */}
          <div className="card flex items-center gap-2">
            <span className="font-semibold">运行状态</span>
            <span className={run.status === "SUCCEEDED" ? "badge-ok"
              : run.status === "PARTIAL" ? "badge-warn" : run.status === "FAILED" ? "badge-err" : "badge-muted"}>
              {run.status}
            </span>
            {run.latency_ms > 0 && <span className="text-xs text-slate-500">耗时 {run.latency_ms} ms</span>}
          </div>

          {/* evidence */}
          {run.evidence?.length > 0 && (
            <div className="card">
              <h3 className="font-semibold mb-2">RAG 证据（instruct #21：仅供证据）</h3>
              <ul className="text-sm space-y-1">
                {run.evidence.map((e: any, i: number) => (
                  <li key={i}>
                    <span className="badge-muted mr-1">{e.trust_tier}</span>
                    {e.title} <span className="text-slate-400">· {e.source_type}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* 西医 — 带 Accept/Edit/Reject */}
            <div className="card">
              <h3 className="font-semibold mb-2 text-brand-700">西医分析</h3>
              <div className="text-sm whitespace-pre-wrap">{run.western?.summary || "（无结构化输出）"}</div>
              {run.western?.differential_diagnoses?.length > 0 && (
                <div className="mt-2 space-y-2">
                  {run.western.differential_diagnoses.map((d: any, i: number) => (
                    <AiSuggestionCard
                      key={i}
                      runId={run.run_id}
                      module="western"
                      idx={i}
                      title={d.diagnosis || d.candidate || d.name}
                      confidence={d.confidence_level}
                      extra={`${d.diagnosis || d.candidate || d.name}`}
                      eid={eid}
                      patientId={encounter?.patient_id || patient?.id || ""}
                      onDiagnosisCreated={refreshDiagnoses}
                    />
                  ))}
                </div>
              )}
            </div>
            {/* 中医 */}
            <div className="card">
              <h3 className="font-semibold mb-2 text-brand-700">中医辨证</h3>
              {run.tcm?.syndrome_candidates?.length > 0 ? (
                <div className="space-y-2">
                  {run.tcm.syndrome_candidates.map((s: any, i: number) => (
                    <AiSuggestionCard
                      key={i}
                      runId={run.run_id}
                      module="tcm"
                      idx={i}
                      title={s.name}
                      confidence={s.confidence_level}
                      extra={s.supporting_evidence?.join("；") || ""}
                      eid={eid}
                      patientId={encounter?.patient_id || patient?.id || ""}
                      onDiagnosisCreated={refreshDiagnoses}
                    />
                  ))}
                </div>
              ) : <div className="text-sm text-slate-500">（无结构化输出）</div>}
              {run.tcm?.treatment_principles && (
                <div className="text-xs mt-2 text-slate-600">
                  治法：{Array.isArray(run.tcm.treatment_principles) ? run.tcm.treatment_principles.join("、") : run.tcm.treatment_principles}
                </div>
              )}
            </div>
          </div>

          {run.synthesis && (run.synthesis.summary || run.synthesis.unified_diagnosis) && (
            <div className="card">
              <h3 className="font-semibold mb-2 text-brand-700">综合评估 · 统一诊断</h3>
              {run.synthesis.unified_diagnosis && (
                <div className="bg-brand-50/60 border border-brand-200 rounded p-3 mb-2 space-y-1 text-sm">
                  {run.synthesis.unified_diagnosis.western_diagnosis && (
                    <div><span className="text-slate-500 font-medium">西医诊断：</span>{run.synthesis.unified_diagnosis.western_diagnosis}</div>
                  )}
                  {run.synthesis.unified_diagnosis.tcm_syndrome && (
                    <div><span className="text-slate-500 font-medium">中医辨证：</span>{run.synthesis.unified_diagnosis.tcm_syndrome}</div>
                  )}
                  {run.synthesis.unified_diagnosis.conflict_notes && (
                    <div className="text-amber-700"><span className="font-medium">冲突提示：</span>{run.synthesis.unified_diagnosis.conflict_notes}</div>
                  )}
                  {run.synthesis.unified_diagnosis.confidence_level && (
                    <div className="text-xs text-slate-400">置信度：{run.synthesis.unified_diagnosis.confidence_level}</div>
                  )}
                </div>
              )}
              {run.synthesis.summary && (
                <div className="text-sm whitespace-pre-wrap">{run.synthesis.summary}</div>
              )}
              {run.synthesis.key_findings?.length > 0 && (
                <ul className="text-xs mt-2 list-disc pl-5 text-slate-700">
                  {run.synthesis.key_findings.map((k: string, i: number) => <li key={i}>{k}</li>)}
                </ul>
              )}
              {run.synthesis.coordination_notes?.length > 0 && (
                <div className="text-xs mt-2 text-slate-600">
                  <b>中西医协调要点：</b>
                  <ul className="list-disc pl-5 mt-1">
                    {run.synthesis.coordination_notes.map((c: string, i: number) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
              )}
              {run.synthesis.recommendations?.length > 0 && (
                <div className="text-xs mt-2 text-slate-600">
                  <b>建议：</b>
                  <ul className="list-disc pl-5 mt-1">
                    {run.synthesis.recommendations.map((r: string, i: number) => <li key={i}>{r}</li>)}
                  </ul>
                </div>
              )}
              {run.synthesis.model_conflicts?.length > 0 && (
                <div className="text-xs mt-2 text-amber-700">
                  <b>模型分歧：</b>
                  <ul className="list-disc pl-5 mt-1">
                    {run.synthesis.model_conflicts.map((m: string, i: number) => <li key={i}>{m}</li>)}
                  </ul>
                </div>
              )}
            </div>
          )}

          {run.tongue && (run.tongue.note !== "no tongue image" && run.tongue.note !== "image not found") && (
            <TongueCard tongue={run.tongue} />
          )}

          {run.prescription && run.prescription.note ? (
            <div className="card border-l-4 border-l-amber-400">
              <h3 className="font-semibold text-amber-700">处方 & 理疗方案</h3>
              <p className="text-sm text-slate-600 mt-1">
                ⏸ 处方和理疗方案已移至"诊断记录"区域。请先采纳诊断，再生成专属治疗方案（约 30-60s）。
              </p>
            </div>
          ) : run.prescription && (
            <PrescriptionCard eid={eid} runId={run.run_id} prescription={run.prescription} allergies={allergies} meds={meds} />
          )}

          {run.therapy && run.therapy.note ? (
            <div className="card border-l-4 border-l-amber-400">
              <h3 className="font-semibold text-amber-700">理疗方案</h3>
              <p className="text-sm text-slate-600 mt-1">
                ⏸ 理疗方案已移至"诊断记录"区域，随处方一起生成。
              </p>
            </div>
          ) : run.therapy && (
            <TherapyCard therapy={run.therapy} runId={run.run_id} eid={eid}
              patientId={encounter?.patient_id || patient?.id || ""} />
          )}
        </div>
      )}

      {/* === 医生编辑文书层 (边框+签字态) === */}
      {/* updatev2_3 §1: always show diagnosis form so doctor can confirm
          diagnoses and generate the single unified treatment plan.
          onTreatmentChange lifts the unified draft up so the §4 effect can
          write it into the medical record editor in real time. */}
      <DiagnosisRecordForm
        eid={eid}
        diagnoses={diagnoses}
        patientId={encounter?.patient_id || patient?.id || ""}
        onRefresh={refreshDiagnoses}
        onTreatmentChange={setUnifiedDraft}
      />

      <section className="card border-l-4 border-l-slate-700">
        <h2 className="font-semibold mb-2 text-slate-800">正式病历编辑器</h2>
        <textarea
          value={mrText}
          onChange={(e) => setMrText(e.target.value)}
          placeholder="主诉、现病史、查体、诊断、处理方案…（保存后落库，重启仍在；幂等不覆盖已编辑版本）"
          className="w-full min-h-[160px] border rounded p-2 text-sm font-mono"
        />
        <div className="mt-2 flex items-center gap-2 flex-wrap">
          <button onClick={saveMedicalRecord} disabled={!mrText} className="btn-ghost">保存病历草稿</button>
          <button onClick={finalizeRecord} className="btn-primary">定稿正式病历 →</button>
          {mrSaved && <span className="text-xs text-slate-500">{mrSaved}</span>}
          {finalizedMsg && <span className="text-xs text-green-600">{finalizedMsg}</span>}
        </div>
        <div className="text-xs text-slate-400 mt-1">
          定稿后从已确认诊断、综合治疗方案、电子处方、理疗审核中自动汇总，形成可恢复的历史版本，导出 PDF。
        </div>
      </section>
    </div>
  );
}

// --- AI 建议 Accept / Edit / Reject (demo.md 第五节) ---
// V1.2 §2: accept/modify creates DiagnosisRecord + triggers per-diagnosis prescription
function AiSuggestionCard({
  runId, module, idx, title, confidence, extra, eid, patientId, onDiagnosisCreated,
}: { runId: string; module: string; idx: number; title: string; confidence?: string;
     extra?: string; eid: string; patientId: string;
     onDiagnosisCreated?: () => void }) {
  const [action, setAction] = useState("");
  const [edit, setEdit] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  // output_id convention: we look it up by polling the run; for simplicity use module+idx
  const oid = `${runId}:${module}:${idx}`;

  async function submit(a: "accept" | "modify" | "reject") {
    setBusy(true);
    try {
      await apiPost(`/api/ai-outputs/${oid}/feedback`, {
        ai_output_id: oid, doctor_id: "dr-001", action: a,
        original_content: title, edited_content: edit, reason: "",
      });
      setAction(a);
      // V1.2 §2: on accept/modify, create diagnosis record
      if (a === "accept" || a === "modify") {
        await apiPost(`/api/encounters/${eid}/diagnoses`, {
          patient_id: patientId,
          doctor_id: "dr-001",
          ai_output_id: oid,
          module,
          western_diagnosis: module === "western" ? (edit || title) : "",
          tcm_syndrome: module === "tcm" ? (edit || title) : "",
          main_evidence: extra || "",
          review_status: a === "accept" ? "accepted" : "modified",
          source: "ai",
        });
        onDiagnosisCreated?.();
      }
    } finally { setBusy(false); setEditing(false); }
  }

  return (
    <div className="border border-brand-200 bg-brand-50/40 rounded p-2 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-medium">{title}</span>
        {confidence && <span className="badge-muted text-xs">{confidence}</span>}
      </div>
      {extra && <div className="text-xs text-slate-600 mt-1">{extra}</div>}
      {!editing && !action && (
        <div className="flex gap-1 mt-1">
          <button onClick={() => submit("accept")} disabled={busy} className="btn-ghost text-xs">接受</button>
          <button onClick={() => setEditing(true)} disabled={busy} className="btn-ghost text-xs">修改</button>
          <button onClick={() => submit("reject")} disabled={busy} className="btn-ghost text-xs">驳回</button>
        </div>
      )}
      {editing && (
        <div className="mt-1 space-y-1">
          <input value={edit} onChange={(e) => setEdit(e.target.value)} placeholder="修改为…"
                 className="w-full border rounded px-2 py-1 text-xs" />
          <div className="flex gap-1">
            <button onClick={() => submit("modify")} disabled={busy || !edit} className="btn-primary text-xs">保存修改</button>
            <button onClick={() => setEditing(false)} className="btn-ghost text-xs">取消</button>
          </div>
        </div>
      )}
      {action && <div className="text-xs mt-1 text-slate-600">已标记：{action === "accept" ? "接受" : action === "modify" ? "已修改" : "已驳回"}</div>}
    </div>
  );
}

// --- 处方草稿 + per-drug review + 签字 + 提交 HIS (demo.md 第七节) ---
function PrescriptionCard({
  eid, runId, prescription, allergies, meds,
}: { eid: string; runId: string; prescription: any; allergies: any[]; meds: any[] }) {
  const [draftId, setDraftId] = useState("");
  const [status, setStatus] = useState("AI_GENERATED");
  const [sigId, setSigId] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [drugActions, setDrugActions] = useState<Record<number, "keep" | "modify" | "delete">>({});

  async function createDraft() {
    setBusy(true);
    try {
      const r = await apiPost<{ id: string }>(`/api/prescriptions/drafts`, {
        encounter_id: eid, patient_id: "", doctor_id: "dr-001",
        ai_run_id: runId, prescription,
      });
      setDraftId(r.id); setStatus("AI_GENERATED");
      setMsg("草稿已生成（AI_GENERATED）");
    } catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  }

  async function confirmDraft() {
    if (!draftId) return;
    setBusy(true);
    try {
      await apiPost(`/api/prescriptions/drafts/${draftId}/confirm`, { doctor_id: "dr-001" });
      setStatus("DOCTOR_CONFIRMED"); setMsg("已确认（DOCTOR_CONFIRMED）");
    } catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  }

  async function sign() {
    if (!draftId) return;
    setBusy(true);
    try {
      const r_sign = await apiPost<{ signature_id: string }>(`/api/prescriptions/drafts/${draftId}/sign`, { doctor_id: "dr-001" });
      setStatus("SIGNED"); setMsg(`已签字 signature_id=${r_sign.signature_id}`);
      setSigId(r_sign.signature_id || "");
    } catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  }

  async function submitHIS() {
    if (!draftId) return;
    setBusy(true);
    try {
      const r = await apiPost(`/api/prescriptions/${draftId}/submit-his`, {
        encounter_id: eid, doctor_id: "dr-001", prescription, signature_id: sigId,
      });
      setStatus("SUBMITTED_TO_HIS"); setMsg(`已提交 MockHIS（未接入真实 HIS，幂等）his_status=${r.his?.his_status}`);
    } catch (e: any) { setMsg(e.message); } finally { setBusy(false); }
  }

  const allergyNames = allergies.map((a: any) => a.allergen);
  const medNames = meds.map((m: any) => m.name);

  return (
    <div className="card border-l-4 border-l-brand-500">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-semibold text-brand-700">处方草稿（AI 候选，非正式处方）</h3>
        <span className="badge-muted">{status}</span>
      </div>
      {prescription.western_medications?.length > 0 && (
        <div className="text-sm mt-2">
          <b>西药：</b>
          <ul className="list-disc pl-5 text-xs space-y-1 mt-1">
            {prescription.western_medications.map((m: any, i: number) => {
              const allergyHit = allergyNames.some(a => a && m.medication?.includes(a));
              const dupHit = medNames.some(mn => mn && m.medication?.includes(mn));
              return (
                <li key={i} className="flex flex-wrap items-center gap-1">
                  <span>{m.medication} {m.dose} {m.frequency} — {m.reason}</span>
                  <span className={m.in_hospital_dict ? "badge-ok" : "badge-warn"}>
                    {m.in_hospital_dict ? "院内" : "院内未配置"}
                  </span>
                  {allergyHit && <span className="badge-err">⚠ 过敏冲突</span>}
                  {dupHit && <span className="badge-warn">⚠ 重复用药</span>}
                  <select
                    value={drugActions[i] || "keep"}
                    onChange={(e) => setDrugActions({ ...drugActions, [i]: e.target.value as any })}
                    className="border rounded text-xs px-1"
                  >
                    <option value="keep">保留</option>
                    <option value="modify">修改</option>
                    <option value="delete">删除</option>
                  </select>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      {prescription.herbal_drafts?.length > 0 && (
        <div className="text-sm mt-2">
          <b>中药：</b>
          <ul className="list-disc pl-5 text-xs mt-1">
            {prescription.herbal_drafts.map((h: any, i: number) => (
              <li key={i}>
                <span className="badge-muted mr-1">{h.formula_type}</span>
                {h.name || "（AI 组方）"}：{h.herbs?.join("、")}
              </li>
            ))}
          </ul>
        </div>
      )}
      {prescription.safety_notes?.length > 0 && (
        <div className="text-xs mt-2 text-red-700">
          <b>安全提示：</b>
          <ul className="list-disc pl-5">
            {prescription.safety_notes.map((s: string, i: number) => <li key={i}>{s}</li>)}
          </ul>
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {!draftId && <button onClick={createDraft} disabled={busy} className="btn-ghost text-xs">生成处方草稿</button>}
        {draftId && status === "AI_GENERATED" && <button onClick={confirmDraft} disabled={busy} className="btn-ghost text-xs">确认草稿</button>}
        {status === "DOCTOR_CONFIRMED" && <button onClick={sign} disabled={busy} className="btn-ghost text-xs">医生签字</button>}
        {status === "SIGNED" && <button onClick={submitHIS} disabled={busy} className="btn-primary text-xs">提交 MockHIS</button>}
        {msg && <span className="text-xs text-slate-500">{msg}</span>}
      </div>
    </div>
  );
}

// --- 舌诊结果展示 (V1.0: tongue analysis output) ---
function TongueCard({ tongue }: { tongue: any }) {
  const tf = tongue.tongue_features || {};
  const iq = tongue.image_quality || {};
  return (
    <div className="card border-l-4 border-l-brand-500">
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-semibold text-brand-700">舌诊分析（AI VLM）</h3>
        <div className="flex items-center gap-2">
          {tongue.model_name && <span className="badge-muted text-xs">{tongue.model_name}</span>}
          {typeof tongue.confidence === "number" && (
            <span className="badge-muted text-xs">置信度 {(tongue.confidence * 100).toFixed(0)}%</span>
          )}
        </div>
      </div>
      {!iq.passed && iq.issues?.length > 0 && (
        <div className="text-xs text-amber-700 bg-amber-50 p-2 rounded mb-2">
          ⚠ 图像质量不佳：{iq.issues.join("；")}
        </div>
      )}
      <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-sm">
        {tf.tongue_color && <div><b>舌色：</b>{tf.tongue_color}</div>}
        {tf.tongue_shape && <div><b>舌形：</b>{tf.tongue_shape}</div>}
        {tf.tongue_body && <div><b>舌体：</b>{tf.tongue_body}</div>}
        {tf.coating_color && <div><b>苔色：</b>{tf.coating_color}</div>}
        {tf.coating_thickness && <div><b>苔厚：</b>{tf.coating_thickness}</div>}
        {tf.coating_texture && <div><b>苔质：</b>{tf.coating_texture}</div>}
        {tf.moisture && <div><b>润燥：</b>{tf.moisture}</div>}
        {tf.greasy === true && <div><b>腻苔：</b>是</div>}
        {tf.teeth_marks === true && <div><b>齿痕：</b>有</div>}
        {tf.cracks === true && <div><b>裂纹：</b>有</div>}
      </div>
      {tongue.observations?.length > 0 && (
        <div className="mt-2 text-sm">
          <b>观察到：</b>
          {tongue.observations.map((s: string, i: number) => (
            <span key={i} className="badge-ok mr-1">{s}</span>
          ))}
        </div>
      )}
      {tongue.uncertain?.length > 0 && (
        <div className="mt-2 text-xs text-slate-600">
          <b>不确定：</b>{tongue.uncertain.join("、")}
        </div>
      )}
      {tongue.not_observed?.length > 0 && (
        <div className="mt-1 text-xs text-slate-400">
          <b>未观察到：</b>{tongue.not_observed.join("、")}
        </div>
      )}
      <div className="text-xs text-slate-400 mt-2">
        舌诊由 clinical_main VLM (Qwen3.8-27B) 真实分析图片，结果仅供中医辨证参考。
      </div>
    </div>
  );
}

// --- 综合治疗方案 (instruct_1.md §4) — 推拿/针灸/艾灸/理疗/康复, 独立于协定方库 ---
// TherapyCard: reviewable therapy with per-item accept/edit/reject (updatev2_2 §5).
// Uses POST /api/therapy/review (TherapyItemReview) when draftId is available;
// falls back to feedback endpoint for read-only main-run display.
function TherapyCard({ therapy, runId = "", eid = "", patientId = "", draftId = "" }: {
  therapy: any; runId?: string; eid?: string; patientId?: string; draftId?: string;
}) {
  const [actions, setActions] = useState<Record<number, string>>({});
  const [showRationale, setShowRationale] = useState<Record<number, boolean>>({});
  const [editing, setEditing] = useState<Record<number, boolean>>({});
  const [editFields, setEditFields] = useState<Record<number, any>>({});
  const [reviews, setReviews] = useState<Record<number, any>>({});

  // §5: load persisted review status when draftId available
  useEffect(() => {
    if (!draftId) return;
    apiGet<{ reviews: any[] }>(`/api/therapy/review?draft_id=${draftId}`)
      .then((res) => {
        const m: Record<number, any> = {};
        for (const r of res.reviews || []) m[r.item_index] = r;
        setReviews(m);
      })
      .catch(() => {});
  }, [draftId]);

  async function submitTherapyReview(idx: number, action: "accept" | "edit" | "reject") {
    const candidate = therapy.candidates[idx];
    if (!draftId) {
      // Fallback to feedback endpoint (read-only, no persistence)
      const oid = runId ? `${runId}:therapy:${idx}` : "";
      if (!oid) { setActions(prev => ({...prev, [idx]: action})); return; }
      try {
        await apiPost(`/api/ai-outputs/${oid}/feedback`, {
          ai_output_id: oid, doctor_id: "dr-001", action,
          original_content: candidate.name, edited_content: "",
          reason: { encounter_id: eid, patient_id: patientId },
        });
        setActions(prev => ({...prev, [idx]: action}));
      } catch (e: any) { alert(`反馈失败: ${e.message}`); }
      return;
    }
    // §5: use TherapyItemReview endpoint
    try {
      const edited = action === "edit" ? editFields[idx] : {};
      const r = await apiPost(`/api/therapy/review`, {
        prescription_draft_id: draftId,
        encounter_id: eid,
        patient_id: patientId,
        item_index: idx,
        action,
        edited_content: edited,
        original_content: candidate,
      });
      setReviews(prev => ({ ...prev, [idx]: r }));
      setActions(prev => ({ ...prev, [idx]: action }));
      setEditing(prev => ({ ...prev, [idx]: false }));
    } catch (e: any) { alert(`审核失败: ${e.message}`); }
  }

  const toggleRationale = (idx: number) =>
    setShowRationale(prev => ({...prev, [idx]: !prev[idx]}));

  const startEdit = (idx: number) => {
    const c = therapy.candidates[idx];
    setEditFields(prev => ({ ...prev, [idx]: {
      name: c.name || "", manipulation: c.manipulation || "",
      acupoints: (c.acupoints || []).join("、"),
      direction_or_stimulation: c.direction_or_stimulation || "",
      duration_or_count: c.duration_or_count || "",
      frequency: c.frequency || "", course: c.course || "",
      contraindications: c.contraindications || "",
    }}));
    setEditing(prev => ({ ...prev, [idx]: true }));
  };

  const reviewStatus = (idx: number) => reviews[idx]?.status;
  const statusBadge = (s?: string) => {
    if (!s) return null;
    const cls: Record<string,string> = {
      AI_GENERATED: "badge-muted", ACCEPTED: "badge-ok",
      EDITED: "badge-ok", REJECTED: "badge-err",
    };
    const label: Record<string,string> = {
      AI_GENERATED: "待审核", ACCEPTED: "已接受",
      EDITED: "已修改", REJECTED: "已驳回",
    };
    return <span className={cls[s] + " text-xs"}>{label[s] || s}</span>;
  };

  if (!therapy || !therapy.candidates?.length) {
    return (
      <div className="card border-l-4 border-l-brand-500">
        <h3 className="font-semibold mb-2 text-brand-700">综合治疗方案</h3>
        <div className="text-sm text-slate-500">（无治疗项目候选）</div>
        {therapy?.lifestyle_advice?.length > 0 && (
          <div className="text-xs mt-2 text-slate-600">
            <b>生活方式建议：</b>
            <ul className="list-disc pl-5">{therapy.lifestyle_advice.map((s:string,i:number)=><li key={i}>{s}</li>)}</ul>
          </div>
        )}
      </div>
    );
  }
  const sourceBadge = (s: string) =>
    s === "hospital" ? "badge-ok" : s === "knowledge" ? "badge-muted" : "badge-warn";
  const sourceLabel = (s: string) =>
    s === "hospital" ? "院内现有" : s === "knowledge" ? "知识库" : "AI 自主";
  return (
    <div className="card border-l-4 border-l-brand-500">
      <h3 className="font-semibold mb-2 text-brand-700">综合治疗方案（推拿/针灸/理疗/康复 · 待医生审核）</h3>
      <ul className="space-y-2 text-sm">
        {therapy.candidates.map((c: any, i: number) => (
          <li key={i} className="border border-brand-200 bg-brand-50/40 rounded p-2">
            <div className="flex items-center justify-between">
              <span className="font-medium">{c.name}</span>
              <div className="flex gap-1">
                {statusBadge(reviewStatus(i))}
                <span className="badge-muted text-xs">{c.category}</span>
                <span className={sourceBadge(c.source_type) + " text-xs"}>
                  {sourceLabel(c.source_type)}
                  {c.source_type === "hospital" && c.hospital_code ? ` ${c.hospital_code}` : ""}
                </span>
              </div>
            </div>
            {c.applicable_scenario && (
              <div className="text-xs text-slate-600 mt-1"><b>适用场景：</b>{c.applicable_scenario}</div>
            )}
            {c.therapeutic_goal && (
              <div className="text-xs text-slate-600 mt-1"><b>治疗目的：</b>{c.therapeutic_goal}</div>
            )}
            {/* §1: 专业结构化理疗字段 — 穴位/定位/手法/方向/时长/频次/疗程 */}
            {c.acupoints?.length > 0 && (
              <div className="text-xs text-slate-700 mt-1">
                <b>穴位：</b>{c.acupoints.join("、")}
              </div>
            )}
            {c.location_basis && (
              <div className={`text-xs mt-1 ${c.location_basis === "待医生确认" ? "text-amber-600" : "text-slate-600"}`}>
                <b>定位依据：</b>{c.location_basis}
              </div>
            )}
            {c.manipulation && (
              <div className={`text-xs mt-1 ${c.manipulation === "待医生确认" ? "text-amber-600" : "text-slate-600"}`}>
                <b>操作手法：</b>{c.manipulation}
              </div>
            )}
            {c.direction_or_stimulation && (
              <div className={`text-xs mt-1 ${c.direction_or_stimulation === "待医生确认" ? "text-amber-600" : "text-slate-600"}`}>
                <b>方向/刺激：</b>{c.direction_or_stimulation}
              </div>
            )}
            {(c.duration_or_count || c.frequency || c.course) && (
              <div className="text-xs mt-1 text-slate-600">
                {c.duration_or_count && <span><b>时长/次数：</b>{c.duration_or_count}　</span>}
                {c.frequency && <span><b>频率：</b>{c.frequency}　</span>}
                {c.course && <span><b>疗程：</b>{c.course}</span>}
              </div>
            )}
            {/* legacy field fallback */}
            {!c.duration_or_count && !c.frequency && !c.course && c.frequency_duration && (
              <div className="text-xs text-slate-600 mt-1"><b>频次/时长：</b>{c.frequency_duration}</div>
            )}
            {c.contraindications && (
              <div className="text-xs text-red-700 mt-1"><b>禁忌：</b>{c.contraindications}</div>
            )}
            {c.rationale && (
              <div className="text-xs text-slate-500 mt-1"><b>依据：</b>{c.rationale}</div>
            )}
            <div className="flex gap-1 mt-1 flex-wrap">
              {!reviewStatus(i) && (
                <>
                  <button onClick={() => submitTherapyReview(i, "accept")} className="btn-ghost text-xs">接受</button>
                  <button onClick={() => startEdit(i)} className="btn-ghost text-xs">修改</button>
                  <button onClick={() => submitTherapyReview(i, "reject")} className="btn-ghost text-xs">驳回</button>
                </>
              )}
              {reviewStatus(i) === "EDITED" && (
                <button onClick={() => startEdit(i)} className="btn-ghost text-xs">继续修改</button>
              )}
              {reviewStatus(i) && (
                <button onClick={() => submitTherapyReview(i, "reject")} className="btn-ghost text-xs">驳回</button>
              )}
              <button onClick={() => toggleRationale(i)} className="btn-ghost text-xs">查看依据</button>
            </div>
            {editing[i] && editFields[i] && (
              <div className="mt-2 space-y-1 border-t pt-2">
                <div className="text-xs font-medium text-slate-600">编辑治疗项（保存后记录原始+修改）</div>
                <input value={editFields[i].name} onChange={(e)=>setEditFields(prev=>({...prev,[i]:{...prev[i],name:e.target.value}}))} placeholder="项目名称" className="w-full border rounded px-2 py-1 text-xs" />
                <input value={editFields[i].acupoints} onChange={(e)=>setEditFields(prev=>({...prev,[i]:{...prev[i],acupoints:e.target.value}}))} placeholder="穴位（顿号分隔）" className="w-full border rounded px-2 py-1 text-xs" />
                <input value={editFields[i].manipulation} onChange={(e)=>setEditFields(prev=>({...prev,[i]:{...prev[i],manipulation:e.target.value}}))} placeholder="操作手法" className="w-full border rounded px-2 py-1 text-xs" />
                <input value={editFields[i].direction_or_stimulation} onChange={(e)=>setEditFields(prev=>({...prev,[i]:{...prev[i],direction_or_stimulation:e.target.value}}))} placeholder="方向/刺激" className="w-full border rounded px-2 py-1 text-xs" />
                <div className="grid grid-cols-3 gap-1">
                  <input value={editFields[i].duration_or_count} onChange={(e)=>setEditFields(prev=>({...prev,[i]:{...prev[i],duration_or_count:e.target.value}}))} placeholder="时长/次数" className="border rounded px-2 py-1 text-xs" />
                  <input value={editFields[i].frequency} onChange={(e)=>setEditFields(prev=>({...prev,[i]:{...prev[i],frequency:e.target.value}}))} placeholder="频率" className="border rounded px-2 py-1 text-xs" />
                  <input value={editFields[i].course} onChange={(e)=>setEditFields(prev=>({...prev,[i]:{...prev[i],course:e.target.value}}))} placeholder="疗程" className="border rounded px-2 py-1 text-xs" />
                </div>
                <input value={editFields[i].contraindications} onChange={(e)=>setEditFields(prev=>({...prev,[i]:{...prev[i],contraindications:e.target.value}}))} placeholder="禁忌" className="w-full border rounded px-2 py-1 text-xs" />
                <div className="flex gap-1">
                  <button onClick={() => submitTherapyReview(i, "edit")} className="btn-primary text-xs">保存修改</button>
                  <button onClick={() => setEditing(prev=>({...prev,[i]:false}))} className="btn-ghost text-xs">取消</button>
                </div>
              </div>
            )}
            {showRationale[i] && c.rationale && (
              <div className="text-xs text-slate-500 mt-1 bg-slate-50 p-1 rounded"><b>详细依据：</b>{c.rationale}</div>
            )}
          </li>
        ))}
      </ul>
      {therapy.lifestyle_advice?.length > 0 && (
        <div className="text-xs mt-3 text-slate-600">
          <b>生活方式与随访建议：</b>
          <ul className="list-disc pl-5 mt-1">{therapy.lifestyle_advice.map((s:string,i:number)=><li key={i}>{s}</li>)}</ul>
        </div>
      )}
      {therapy.safety_notes?.length > 0 && (
        <div className="text-xs mt-2 text-red-700">
          <b>安全提示：</b>
          <ul className="list-disc pl-5">{therapy.safety_notes.map((s:string,i:number)=><li key={i}>{s}</li>)}</ul>
        </div>
      )}
      <div className="text-xs text-slate-400 mt-2">来源区分：院内现有项目 / 知识库方案 / AI 自主建议。AI 自主建议无院内编码。</div>
    </div>
  );
}

function _moduleLabel(k: string) {
  return ({ western: "西医", tcm: "中医", tongue: "舌诊", knowledge: "知识",
            synthesis: "综合", prescription: "处方", therapy: "理疗方案" } as Record<string, string>)[k] || k;
}
function _statusLabel(v: string) {
  return ({ pending: "等待", running: "分析中", complete: "完成",
            failed: "失败", ok: "完成", unavailable: "不可用",
            skipped: "跳过" } as Record<string, string>)[v] || v;
}


// --- updatev2_3 §1: Diagnosis record form — single unified treatment plan ---
function DiagnosisRecordForm({
  eid, diagnoses, patientId, onRefresh, onTreatmentChange,
}: { eid: string; diagnoses: any[]; patientId: string;
     onRefresh: () => void; onTreatmentChange?: (d: any) => void }) {
  // Single encounter-level draft (replaces per-diagnosis drafts)
  const [unifiedDraftId, setUnifiedDraftId] = useState("");
  const [unifiedDraft, setUnifiedDraft] = useState<any>(null);
  const [unifiedGenerating, setUnifiedGenerating] = useState(false);
  const [staleWarning, setStaleWarning] = useState(false);

  // Lift the unified draft up to the parent so the medical record editor §4
  // effect can sync the 【综合治疗方案】 block in real time.
  useEffect(() => { onTreatmentChange?.(unifiedDraft); }, [unifiedDraft, onTreatmentChange]);

  const confirmedCount = diagnoses.filter(
    (d) => d.review_status === "accepted" || d.review_status === "modified").length;

  // Load existing unified draft on mount / diagnoses change
  useEffect(() => {
    apiGet<{ draft: any; has_legacy: boolean }>(
      `/api/encounters/${eid}/treatment/latest`)
      .then((res) => {
        if (res.draft) {
          setUnifiedDraftId(res.draft.id);
          setUnifiedDraft(res.draft.structured_json || null);
          setStaleWarning(!!res.draft.is_stale);
        }
      })
      .catch(() => {});
  }, [eid, diagnoses.length]);

  async function generateUnifiedTreatment() {
    setUnifiedGenerating(true);
    try {
      const r = await apiPost<{ prescription_id: string; reused?: boolean }>(
        `/api/encounters/${eid}/treatment`, { doctor_id: "dr-001" });
      const pid = r.prescription_id;
      setUnifiedDraftId(pid);
      setStaleWarning(false);
      if (r.reused) {
        // Already exists with matching snapshot — load it directly
        const draft = await apiGet<{ structured_json: any; status: string }>(
          `/api/prescriptions/drafts/${pid}`);
        setUnifiedDraft(draft.structured_json || null);
        setUnifiedGenerating(false);
        return;
      }
      // Poll for completion
      const poll = setInterval(async () => {
        try {
          const draft = await apiGet<{ id: string; status: string; structured_json: any; is_stale?: boolean }>(
            `/api/prescriptions/drafts/${pid}`);
          const sj = draft.structured_json || {};
          const hasContent = !!sj.prescription || !!sj.therapy;
          if (hasContent && (draft.status === "AI_GENERATED" || draft.status === "DOCTOR_EDITING")) {
            clearInterval(poll);
            setUnifiedDraft(sj);
            setStaleWarning(!!draft.is_stale);
            setUnifiedGenerating(false);
          }
        } catch { /* retry on next interval */ }
      }, 3000);
      setTimeout(() => {
        clearInterval(poll);
        setUnifiedGenerating((prev) => {
          if (prev) alert("综合治疗方案生成超时（120s），请刷新重试。");
          return false;
        });
      }, 120000);
    } catch (e: any) {
      alert(`综合治疗方案生成失败: ${e.message}`);
      setUnifiedGenerating(false);
    }
  }

  async function updateDiagnosis(did: string, status: string) {
    try {
      await apiPatch(`/api/encounters/${eid}/diagnoses/${did}`, {
        review_status: status,
      });
      onRefresh();
    } catch (e: any) {
      alert(`更新失败: ${e.message}`);
    }
  }

  const statusBadge = (s: string) => {
    const map: Record<string, string> = {
      pending: "badge-warn", accepted: "badge-ok",
      modified: "badge-ok", rejected: "badge-err",
    };
    return map[s] || "badge-muted";
  };
  const statusLabel = (s: string) =>
    ({ pending: "待审核", accepted: "已接受", modified: "已修改",
       rejected: "已驳回" } as Record<string, string>)[s] || s;

  return (
    <section className="card border-l-4 border-l-brand-500">
      <h2 className="font-semibold mb-2 text-brand-700">最终诊断列表（AI候选 → 医生审核）</h2>
      <div className="space-y-3">
        {diagnoses.map((d, i) => (
          <div key={d.id} className="border border-brand-200 bg-brand-50/40 rounded p-3 text-sm">
            <div className="flex items-center justify-between">
              <div>
                <span className="font-medium">
                  {d.module === "western" ? "西医诊断" : "中医证型"}：
                  {d.western_diagnosis || d.tcm_syndrome}
                </span>
              </div>
              <span className={statusBadge(d.review_status) + " text-xs"}>
                {statusLabel(d.review_status)}
              </span>
            </div>
            {d.main_evidence && (
              <div className="text-xs text-slate-600 mt-1"><b>主要依据：</b>{d.main_evidence}</div>
            )}
            {d.treatment_goal && (
              <div className="text-xs text-slate-600 mt-1"><b>治疗目标：</b>{d.treatment_goal}</div>
            )}
            {d.doctor_notes && (
              <div className="text-xs text-slate-600 mt-1"><b>医生备注：</b>{d.doctor_notes}</div>
            )}
            <div className="text-xs text-slate-400 mt-1">
              来源：{d.source === "ai" ? "AI候选" : "医生录入"} · {d.created_at?.slice(0, 19)}
            </div>
            <div className="flex gap-1 mt-2 flex-wrap">
              {d.review_status === "pending" && (
                <>
                  <button onClick={() => updateDiagnosis(d.id, "accepted")}
                    className="btn-ghost text-xs">接受</button>
                  <button onClick={() => updateDiagnosis(d.id, "rejected")}
                    className="btn-ghost text-xs">驳回</button>
                </>
              )}
              {d.review_status === "accepted" && (
                <button onClick={() => updateDiagnosis(d.id, "pending")}
                  className="btn-ghost text-xs">撤回</button>
              )}
            </div>
          </div>
        ))}
        {diagnoses.length === 0 && (
          <div className="text-sm text-slate-400 py-4 text-center">暂无诊断记录。AI 分析后将生成诊断候选。</div>
        )}
      </div>

      {/* §1: Single unified "Generate Comprehensive Treatment Plan" button */}
      <div className="mt-4 border-t pt-3">
        {staleWarning && (
          <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2 mb-2">
            ⚠ 诊断已变更，旧治疗方案已标记过期。请重新生成综合治疗方案以匹配当前诊断。
          </div>
        )}
        <button
          onClick={generateUnifiedTreatment}
          disabled={confirmedCount === 0 || unifiedGenerating}
          className="btn-primary text-sm">
          {unifiedGenerating
            ? `⏳ 正在生成综合治疗方案（约 30-60s）…`
            : unifiedDraft
              ? "重新生成综合治疗方案"
              : `生成综合治疗方案（${confirmedCount} 个已确认诊断）→`}
        </button>
        {confirmedCount === 0 && (
          <span className="text-xs text-slate-400 ml-2">请先接受或修改至少一个诊断</span>
        )}
      </div>

      {/* Single unified PrescriptionForm + TherapyCard (not per-diagnosis) */}
      {unifiedDraft && (
        <div className="mt-3 space-y-3">
          {unifiedDraft.prescription && (
            <PrescriptionForm
              encounterId={eid}
              patientId={patientId}
              doctorId="dr-001"
              targetDiagnosisId=""
              draftId={unifiedDraftId}
              initialPrescription={unifiedDraft.prescription}
              onSaved={() => onRefresh()}
            />
          )}
          {unifiedDraft.therapy && (
            <TherapyCard
              therapy={unifiedDraft.therapy}
              runId=""
              eid={eid}
              patientId={patientId}
              draftId={unifiedDraftId}
            />
          )}
        </div>
      )}
      <div className="text-xs text-slate-400 mt-2">
        updatev2_3 §1：全部已确认诊断统一生成一份综合治疗方案（整合西药/中成药/中药饮片/经方/理疗），避免重复用药与矛盾治疗。
        诊断变更时旧方案自动标记过期。可编辑药品克数、增删药味，逐项审核理疗。
      </div>
    </section>
  );
}
