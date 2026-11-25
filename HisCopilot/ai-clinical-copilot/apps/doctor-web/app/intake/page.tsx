"use client";

import { useState, useEffect, Suspense } from "react";
import { apiPost, apiUpload, API_BASE } from "@/lib/api";
import VoiceRecorder from "@/components/VoiceRecorder";
import LabUpload from "@/components/LabUpload";
import { useRouter, useSearchParams } from "next/navigation";

// 患者自主问诊入口 — V1.0 updatev1_1.md 入口A
// V1.2 §6: accept ?d=token for doctor QR binding
// Mobile-first multi-step: patient info → condition → lab upload → tongue → preview → submit

type Step = "info" | "condition" | "labs" | "tongue" | "preview" | "done";

function IntakeContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [step, setStep] = useState<Step>("info");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [encounterId, setEncounterId] = useState("");

  // V1.2 §6: doctor binding from QR token
  const [doctorId, setDoctorId] = useState("");
  const [doctorName, setDoctorName] = useState("");

  // Patient info
  const [name, setName] = useState("");
  const [gender, setGender] = useState("");
  const [age, setAge] = useState("");
  const [phone, setPhone] = useState("");
  const [insurance, setInsurance] = useState("");
  const [allergies, setAllergies] = useState("");

  // Condition
  const [chiefComplaint, setChiefComplaint] = useState("");
  const [rawText, setRawText] = useState("");
  const [symptoms, setSymptoms] = useState("");

  // Labs
  const [labs, setLabs] = useState<any[]>([]);
  const [labText, setLabText] = useState("");

  // Tongue
  const [tongueKey, setTongueKey] = useState("");
  const [tonguePreview, setTonguePreview] = useState("");

  // Patient search
  const [existingPatient, setExistingPatient] = useState<any>(null);

  // V1.2 §6: validate QR token on mount
  useEffect(() => {
    const token = searchParams.get("d");
    if (token) {
      fetch(`${API_BASE}/api/qr/${token}`)
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (d) {
            setDoctorId(d.doctor_id);
            setDoctorName(d.doctor_name);
          }
        })
        .catch(() => {});
    }
  }, [searchParams]);

  async function searchByPhone() {
    if (!phone) return;
    setErr("");
    try {
      const r = await fetch(`${API_BASE}/api/patients/search/phone/${phone}`);
      if (r.ok) {
        const p = await r.json();
        if (p && p.id) {
          setExistingPatient(p);
          setName(p.name || "");
          setGender(p.gender || "");
          setAge(String(p.age || ""));
          if (p.allergies?.length) {
            setAllergies(p.allergies.map((a: any) => a.allergen).join("、"));
          }
        }
      }
    } catch {}
  }

  // V1.2 §1: upload tongue image via standalone endpoint (compressed copy generated)
  async function uploadTongue(file: File) {
    setErr("");
    setTonguePreview(URL.createObjectURL(file));
    try {
      const formData = new FormData();
      formData.append("file", file);
      const r = await apiUpload<{ key: string; original_url: string }>(
        `/api/tongue-images`, formData);
      // key is the compressed key — used for model input
      setTongueKey(r.key);
      setTonguePreview(`${API_BASE}${r.original_url}`);
    } catch (e: any) {
      setErr(`舌象上传失败: ${e.message}`);
    }
  }

  async function submit() {
    setBusy(true);
    setErr("");
    try {
      // 1. Create patient (if not existing)
      let pid = existingPatient?.id;
      if (!pid) {
        const allergyList = allergies
          .split(/[、,，]/).map(s => s.trim()).filter(Boolean)
          .map(a => ({ allergen: a, severity: "未明确" }));
        const patient = await apiPost("/api/patients", {
          name, gender, age: parseInt(age) || 0, phone,
          allergies: allergyList, insurance,
        });
        pid = patient.id;
      }

      // 2. Create encounter with source=patient, V1.2 §6: bind doctor if from QR
      const symptomList = symptoms.split(/[、,，]/).map(s => s.trim()).filter(Boolean);
      const enc = await apiPost("/api/encounters", {
        patient_id: pid,
        doctor_id: doctorId || undefined,
        chief_complaint: chiefComplaint,
        raw_text: rawText,
        history: { symptoms: symptomList },
        allergies: (allergies.split(/[、,，]/).map(s => s.trim()).filter(Boolean))
          .map(a => ({ allergen: a, severity: "未明确" })),
        labs,
        source: "patient",
        case_type: "患者自主问诊",
        tongue_image_key: tongueKey,
      });
      setEncounterId(enc.id);
      setStep("done");
    } catch (e: any) {
      setErr(`提交失败: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  const steps: { key: Step; label: string }[] = [
    { key: "info", label: "患者信息" },
    { key: "condition", label: "病情描述" },
    { key: "labs", label: "检验报告" },
    { key: "tongue", label: "舌象" },
    { key: "preview", label: "预览提交" },
  ];

  return (
    <div className="space-y-4">
      {/* V1.2 §6: doctor binding banner */}
      {doctorName && (
        <div className="card bg-brand-50 border-brand-200 text-sm text-brand-800">
          📋 本次问诊将绑定医生：<b>{doctorName}</b>（{doctorId}）
        </div>
      )}
      {/* Progress steps */}
      {step !== "done" && (
        <div className="flex items-center gap-1 text-xs">
          {steps.map((s, i) => (
            <span key={s.key} className="flex items-center gap-1">
              <span className={`px-2 py-1 rounded ${step === s.key ? "bg-brand-600 text-white" : "bg-slate-200 text-slate-600"}`}>
                {i + 1}. {s.label}
              </span>
              {i < steps.length - 1 && <span className="text-slate-300">→</span>}
            </span>
          ))}
        </div>
      )}

      {err && <div className="text-sm text-red-600 bg-red-50 p-2 rounded">{err}</div>}

      {/* Step: patient info */}
      {step === "info" && (
        <div className="card space-y-3">
          <h2 className="font-semibold">患者信息</h2>
          <div>
            <label className="text-xs text-slate-500 block mb-1">手机号（用于查找已有档案）</label>
            <div className="flex gap-2">
              <input value={phone} onChange={e => setPhone(e.target.value)}
                className="flex-1 border rounded px-3 py-2 text-sm"
                placeholder="请输入手机号" />
              <button onClick={searchByPhone} className="btn-ghost text-xs">查找</button>
            </div>
            {existingPatient && (
              <div className="text-xs text-green-600 mt-1">✅ 已找到档案: {existingPatient.name}</div>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-slate-500 block mb-1">姓名 *</label>
              <input value={name} onChange={e => setName(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="text-xs text-slate-500 block mb-1">性别</label>
              <select value={gender} onChange={e => setGender(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm">
                <option value="">请选择</option>
                <option value="男">男</option>
                <option value="女">女</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-slate-500 block mb-1">年龄</label>
              <input type="number" value={age} onChange={e => setAge(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="text-xs text-slate-500 block mb-1">医保</label>
              <input value={insurance} onChange={e => setInsurance(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm" placeholder="自费/医保" />
            </div>
          </div>
          <div>
            <label className="text-xs text-slate-500 block mb-1">过敏史（如有）</label>
            <input value={allergies} onChange={e => setAllergies(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm" placeholder="如：青霉素、海鲜" />
          </div>
          <div className="flex justify-end">
            <button onClick={() => setStep("condition")} disabled={!name}
              className="btn-primary text-sm">下一步 →</button>
          </div>
        </div>
      )}

      {/* Step: condition */}
      {step === "condition" && (
        <div className="card space-y-3">
          <h2 className="font-semibold">病情描述</h2>
          <div>
            <label className="text-xs text-slate-500 block mb-1">主诉（主要症状+持续时间）*</label>
            <textarea value={chiefComplaint} onChange={e => setChiefComplaint(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm min-h-16"
              placeholder="如：反复头痛3月，加重伴恶心1周" />
            <div className="mt-1"><VoiceRecorder onTranscribe={t => setChiefComplaint(prev => prev ? `${prev} ${t}` : t)} /></div>
          </div>
          <div>
            <label className="text-xs text-slate-500 block mb-1">详细病情（现病史）</label>
            <textarea value={rawText} onChange={e => setRawText(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm min-h-24"
              placeholder="请详细描述发病过程、症状特点、加重/缓解因素等" />
            <div className="mt-1"><VoiceRecorder onTranscribe={t => setRawText(prev => prev ? `${prev} ${t}` : t)} /></div>
          </div>
          <div>
            <label className="text-xs text-slate-500 block mb-1">伴随症状（顿号分隔）</label>
            <input value={symptoms} onChange={e => setSymptoms(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm" placeholder="如：头痛、恶心、失眠" />
          </div>
          <div className="flex justify-between">
            <button onClick={() => setStep("info")} className="btn-ghost text-sm">← 上一步</button>
            <button onClick={() => setStep("labs")} disabled={!chiefComplaint}
              className="btn-primary text-sm">下一步 →</button>
          </div>
        </div>
      )}

      {/* Step: labs */}
      {step === "labs" && (
        <div className="card space-y-3">
          <h2 className="font-semibold">检验报告（可选）</h2>
          <p className="text-xs text-slate-500">上传检验报告图片，系统将自动识别内容。也可跳过此步。</p>
          <LabUpload onParsed={(parsed, text) => { setLabs(parsed); setLabText(text); }} />
          {labs.length > 0 && (
            <div className="text-xs text-slate-600">
              已识别 {labs.length} 项检验指标
            </div>
          )}
          <div className="flex justify-between">
            <button onClick={() => setStep("condition")} className="btn-ghost text-sm">← 上一步</button>
            <button onClick={() => setStep("tongue")} className="btn-primary text-sm">下一步 →</button>
          </div>
        </div>
      )}

      {/* Step: tongue */}
      {step === "tongue" && (
        <div className="card space-y-3">
          <h2 className="font-semibold">舌象图片（可选）</h2>
          <p className="text-xs text-slate-500">拍摄或上传舌象照片，供中医辨证参考。系统会生成压缩副本供模型分析，同时保留原图供医生查看。</p>
          <input type="file" accept="image/png,image/jpeg,image/webp"
            onChange={e => {
              const f = e.target.files?.[0];
              if (f) uploadTongue(f);
            }}
            className="text-xs" />
          {tonguePreview && <img src={tonguePreview} alt="舌象" className="max-h-40 rounded" />}
          {tongueKey && <div className="text-xs text-green-600">✅ 舌象已上传（压缩副本已生成，模型将使用压缩版分析）</div>}
          <div className="flex justify-between">
            <button onClick={() => setStep("labs")} className="btn-ghost text-sm">← 上一步</button>
            <button onClick={() => setStep("preview")} className="btn-primary text-sm">下一步 →</button>
          </div>
        </div>
      )}

      {/* Step: preview */}
      {step === "preview" && (
        <div className="card space-y-3">
          <h2 className="font-semibold">预览并提交</h2>
          <div className="text-sm space-y-2">
            <div><span className="text-slate-500">姓名:</span> {name} {gender} {age}岁</div>
            <div><span className="text-slate-500">手机:</span> {phone || "—"}</div>
            <div><span className="text-slate-500">主诉:</span> {chiefComplaint}</div>
            {rawText && <div><span className="text-slate-500">现病史:</span> {rawText}</div>}
            {symptoms && <div><span className="text-slate-500">症状:</span> {symptoms}</div>}
            {allergies && <div><span className="text-slate-500">过敏:</span> {allergies}</div>}
            {labs.length > 0 && <div><span className="text-slate-500">检验:</span> {labs.length} 项</div>}
            {tongueKey && <div><span className="text-slate-500">舌象:</span> 已上传</div>}
          </div>
          <div className="flex justify-between">
            <button onClick={() => setStep("tongue")} className="btn-ghost text-sm">← 上一步</button>
            <button onClick={submit} disabled={busy} className="btn-primary text-sm">
              {busy ? "提交中…" : "✓ 提交给医生"}
            </button>
          </div>
        </div>
      )}

      {/* Done */}
      {step === "done" && (
        <div className="card text-center space-y-4 py-8">
          <div className="text-4xl">✅</div>
          <h2 className="font-semibold text-lg">提交成功</h2>
          <p className="text-sm text-slate-600">您的就诊信息已提交，请等待医生接诊。</p>
          <div className="text-xs text-slate-400">就诊号: {encounterId}</div>
          <div className="flex justify-center gap-3 pt-2">
            <button
              type="button"
              onClick={() => window.close()}
              className="btn-primary text-sm"
            >
              完成
            </button>
          </div>
          <p className="text-xs text-slate-400 pt-1">
            医生将在工作站中查看您的信息并进行分析，结果仅医生可见。
          </p>
        </div>
      )}
    </div>
  );
}

export default function IntakePage() {
  return (
    <Suspense fallback={<div className="text-sm text-slate-400">加载中…</div>}>
      <IntakeContent />
    </Suspense>
  );
}
