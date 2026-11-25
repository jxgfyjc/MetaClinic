"use client";

import { useState, useEffect } from "react";
import { apiPost, apiUpload, apiGet, API_BASE } from "@/lib/api";
import VoiceRecorder from "@/components/VoiceRecorder";
import LabUpload from "@/components/LabUpload";
import { useRouter, useSearchParams } from "next/navigation";

// 医生直接采集入口 — V1.0 updatev1_1.md 入口B
// Search existing patient or create new + create encounter with full clinical context
// updatev2_3 §4d: honor ?pid= param — preload patient from patient detail page "新建就诊"

export default function NewEncounterPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pidParam = searchParams.get("pid") || "";
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // Patient search/create
  const [searchPhone, setSearchPhone] = useState("");
  const [foundPatient, setFoundPatient] = useState<any>(null);
  const [isNewPatient, setIsNewPatient] = useState(false);
  const [name, setName] = useState("");
  const [gender, setGender] = useState("");
  const [age, setAge] = useState("");
  const [phone, setPhone] = useState("");
  const [insurance, setInsurance] = useState("");
  const [allergies, setAllergies] = useState("");
  const [chronicConditions, setChronicConditions] = useState("");
  const [currentMeds, setCurrentMeds] = useState("");

  // §4d: if pid in URL, preload patient on mount
  useEffect(() => {
    if (!pidParam) return;
    (async () => {
      try {
        const p = await apiGet<any>(`/api/patients/${pidParam}`);
        if (p && p.id) {
          setFoundPatient(p);
          setName(p.name || "");
          setGender(p.gender || "");
          setAge(String(p.age || ""));
          setPhone(p.phone || "");
          setInsurance(p.insurance || "");
          if (p.allergies?.length) {
            setAllergies(p.allergies.map((a: any) => a.allergen).join("、"));
          }
          if (p.chronic_conditions) {
            setChronicConditions(
              Array.isArray(p.chronic_conditions)
                ? p.chronic_conditions.join("、")
                : String(p.chronic_conditions)
            );
          }
        }
      } catch (e: any) {
        setErr(`预填患者失败: ${e.message}`);
      }
    })();
  }, [pidParam]);

  // Clinical context
  const [chiefComplaint, setChiefComplaint] = useState("");
  const [presentIllness, setPresentIllness] = useState("");
  const [symptoms, setSymptoms] = useState("");

  // Labs
  const [labs, setLabs] = useState<any[]>([]);

  // Tongue
  const [tonguePreview, setTonguePreview] = useState("");
  const [tongueFile, setTongueFile] = useState<File | null>(null);
  const [tongueUploaded, setTongueUploaded] = useState(false);

  async function searchPatient() {
    if (!searchPhone) return;
    setErr("");
    setFoundPatient(null);
    setIsNewPatient(false);
    try {
      const r = await fetch(`${API_BASE}/api/patients/search/phone/${searchPhone}`);
      if (r.ok) {
        const p = await r.json();
        if (p && p.id) {
          setFoundPatient(p);
          setName(p.name || "");
          setGender(p.gender || "");
          setAge(String(p.age || ""));
          setPhone(p.phone || searchPhone);
          if (p.allergies?.length) {
            setAllergies(p.allergies.map((a: any) => a.allergen).join("、"));
          }
        } else {
          setIsNewPatient(true);
          setPhone(searchPhone);
        }
      } else {
        setIsNewPatient(true);
        setPhone(searchPhone);
      }
    } catch (e: any) {
      setErr(`查找失败: ${e.message}`);
    }
  }

  async function submit() {
    setBusy(true);
    setErr("");
    try {
      let pid = foundPatient?.id;
      if (!pid) {
        const allergyList = allergies
          .split(/[、,，]/).map(s => s.trim()).filter(Boolean)
          .map(a => ({ allergen: a, severity: "未明确" }));
        const patient = await apiPost("/api/patients", {
          name, gender, age: parseInt(age) || 0, phone,
          allergies: allergyList, insurance,
          chronic_conditions: chronicConditions,
        });
        pid = patient.id;
      }

      const symptomList = symptoms.split(/[、,，]/).map(s => s.trim()).filter(Boolean);
      const medList = currentMeds.split(/[、,，]/).map(s => s.trim()).filter(Boolean);

      // §3d: upload tongue image first (if selected), get key for encounter
      let tongueImageKey: string | undefined;
      if (tongueFile) {
        try {
          const fd = new FormData();
          fd.append("file", tongueFile);
          fd.append("patient_id", pid!);
          const tr = await apiUpload<{ key: string; image_key?: string }>("/api/tongue-images", fd);
          tongueImageKey = tr.image_key || tr.key;
        } catch (e: any) {
          // non-fatal: tongue is optional, continue without it
          console.warn("tongue upload failed:", e.message);
        }
      }

      const enc = await apiPost("/api/encounters", {
        patient_id: pid,
        chief_complaint: chiefComplaint,
        raw_text: presentIllness,
        history: { symptoms: symptomList, chronic_conditions: chronicConditions },
        medications: medList.map(m => ({ name: m, dosage: "", frequency: "" })),
        allergies: (allergies.split(/[、,，]/).map(s => s.trim()).filter(Boolean))
          .map(a => ({ allergen: a, severity: "未明确" })),
        labs,
        tongue_image_key: tongueImageKey,
        source: "doctor",
        case_type: "医生直接采集",
      });
      router.push(`/encounters/${enc.id}`);
    } catch (e: any) {
      setErr(`提交失败: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  const ready = chiefComplaint && (foundPatient || (isNewPatient && name));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">新建就诊</h1>
        <button onClick={() => router.back()} className="btn-ghost text-xs">← 返回</button>
      </div>

      {err && <div className="text-sm text-red-600 bg-red-50 p-2 rounded">{err}</div>}

      {/* Patient selection */}
      <div className="card space-y-3">
        <h2 className="font-semibold">1. 患者选择</h2>
        <div className="flex gap-2">
          <input
            value={searchPhone}
            onChange={e => setSearchPhone(e.target.value)}
            className="flex-1 border rounded px-3 py-2 text-sm"
            placeholder="输入手机号查找已有患者"
          />
          <button onClick={searchPatient} className="btn-ghost text-sm">查找</button>
          <button
            onClick={() => { setIsNewPatient(true); setFoundPatient(null); setPhone(searchPhone); }}
            className="btn-ghost text-sm"
          >+ 新建</button>
        </div>

        {foundPatient && (
          <div className="text-sm bg-green-50 p-2 rounded">
            ✅ 已选患者: <b>{foundPatient.name}</b> {foundPatient.gender} {foundPatient.age}岁 — {foundPatient.id}
          </div>
        )}

        {(isNewPatient || (foundPatient === null && searchPhone)) && !foundPatient && (
          <div className="grid grid-cols-3 gap-3 border-t pt-3">
            <div>
              <label className="text-xs text-slate-500 block mb-1">姓名 *</label>
              <input value={name} onChange={e => setName(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="text-xs text-slate-500 block mb-1">性别</label>
              <select value={gender} onChange={e => setGender(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm">
                <option value="">选择</option>
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
              <label className="text-xs text-slate-500 block mb-1">手机</label>
              <input value={phone} onChange={e => setPhone(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="text-xs text-slate-500 block mb-1">医保</label>
              <input value={insurance} onChange={e => setInsurance(e.target.value)}
                className="w-full border rounded px-3 py-2 text-sm" placeholder="自费/医保" />
            </div>
          </div>
        )}

        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="text-xs text-slate-500 block mb-1">过敏史</label>
            <input value={allergies} onChange={e => setAllergies(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm" placeholder="如：青霉素" />
          </div>
          <div>
            <label className="text-xs text-slate-500 block mb-1">慢病史</label>
            <input value={chronicConditions} onChange={e => setChronicConditions(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm" placeholder="如：高血压、颈椎病" />
          </div>
          <div>
            <label className="text-xs text-slate-500 block mb-1">现用药</label>
            <input value={currentMeds} onChange={e => setCurrentMeds(e.target.value)}
              className="w-full border rounded px-3 py-2 text-sm" placeholder="如：氨氯地平 5mg" />
          </div>
        </div>
      </div>

      {/* Chief complaint + present illness */}
      <div className="card space-y-3">
        <h2 className="font-semibold">2. 主诉与现病史</h2>
        <div>
          <label className="text-xs text-slate-500 block mb-1">主诉 *</label>
          <textarea value={chiefComplaint} onChange={e => setChiefComplaint(e.target.value)}
            className="w-full border rounded px-3 py-2 text-sm min-h-16"
            placeholder="如：反复头痛3月，加重伴恶心1周" />
          <div className="mt-1"><VoiceRecorder onTranscribe={t => setChiefComplaint(prev => prev ? `${prev} ${t}` : t)} /></div>
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1">现病史</label>
          <textarea value={presentIllness} onChange={e => setPresentIllness(e.target.value)}
            className="w-full border rounded px-3 py-2 text-sm min-h-28"
            placeholder="发病过程、症状特点、加重/缓解因素、既往诊疗经过" />
          <div className="mt-1"><VoiceRecorder onTranscribe={t => setPresentIllness(prev => prev ? `${prev} ${t}` : t)} /></div>
        </div>
        <div>
          <label className="text-xs text-slate-500 block mb-1">伴随症状（顿号分隔）</label>
          <input value={symptoms} onChange={e => setSymptoms(e.target.value)}
            className="w-full border rounded px-3 py-2 text-sm" placeholder="如：头痛、恶心、失眠" />
        </div>
      </div>

      {/* Labs */}
      <div className="card space-y-3">
        <h2 className="font-semibold">3. 检验报告（可选）</h2>
        <LabUpload onParsed={(parsed) => setLabs(parsed)} />
        {labs.length > 0 && (
          <div className="text-xs text-slate-600">已识别 {labs.length} 项检验指标</div>
        )}
      </div>

      {/* Tongue */}
      <div className="card space-y-3">
        <h2 className="font-semibold">4. 舌象图片（可选）</h2>
        <input type="file" accept="image/png,image/jpeg,image/webp"
          onChange={e => {
            const f = e.target.files?.[0];
            if (f) {
              setTonguePreview(URL.createObjectURL(f));
              setTongueFile(f);
              setTongueUploaded(true);
            }
          }}
          className="text-xs" />
        {tonguePreview && <img src={tonguePreview} alt="舌象" className="max-h-32 rounded" />}
        {tongueUploaded && tongueFile && (
          <div className="text-xs text-green-600">✓ 已选择舌象，提交时将上传至 VLM 分析</div>
        )}
      </div>

      {/* Submit */}
      <div className="flex justify-end gap-2">
        <button onClick={() => router.back()} className="btn-ghost text-sm">取消</button>
        <button onClick={submit} disabled={!ready || busy} className="btn-primary text-sm">
          {busy ? "创建中…" : "✓ 创建就诊并进入诊疗"}
        </button>
      </div>
    </div>
  );
}
