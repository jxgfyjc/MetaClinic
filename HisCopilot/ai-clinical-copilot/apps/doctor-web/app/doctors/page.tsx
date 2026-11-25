"use client";

import { useState, useEffect, useRef } from "react";
import { apiGet, apiPost, apiPatch, apiUpload, API_BASE, type DoctorProfile } from "@/lib/api";
import Link from "next/link";
import QRCodeImage from "@/components/QRCodeImage";

// 医师风格管理 + V1.2 §6 QR code + V1.2 §5 case mining / experience patterns

export default function DoctorsPage() {
  const [doctors, setDoctors] = useState<{ id: string; name: string; dept?: string }[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [profile, setProfile] = useState<DoctorProfile | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  // V1.2 §6: QR code
  const [qrUrl, setQrUrl] = useState("");
  const [qrToken, setQrToken] = useState("");

  // V1.2 §5: case import
  const [importId, setImportId] = useState("");
  const [importProgress, setImportProgress] = useState<any>(null);
  const [patterns, setPatterns] = useState<any[]>([]);
  const [caseText, setCaseText] = useState("");
  const [caseImages, setCaseImages] = useState<File[]>([]);
  const [importMode, setImportMode] = useState<"text" | "images">("images");
  const imgRef = useRef<HTMLInputElement>(null);

  const [form, setForm] = useState<DoctorProfile>({
    doctor_id: "", dept: "", specialties: [],
    syndrome_preference: "", formula_preference: "",
    herb_preference: "", non_drug_preference: "",
    analysis_focus: "", experience_notes: [],
  });

  useEffect(() => {
    apiGet<{ id: string; name: string; dept?: string }[]>("/api/doctors").then((d) => {
      setDoctors(d);
      if (d.length > 0) setSelectedId(d[0].id);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    setEditing(false); setMsg(""); setQrUrl(""); setQrToken("");
    setImportId(""); setImportProgress(null); setPatterns([]);
    apiGet<DoctorProfile>(`/api/doctors/${selectedId}/profile`).then((p) => {
      setProfile(p); setForm(p);
    }).catch(() => {
      setProfile(null);
      setForm({
        doctor_id: selectedId, dept: "", specialties: [],
        syndrome_preference: "", formula_preference: "",
        herb_preference: "", non_drug_preference: "",
        analysis_focus: "", experience_notes: [],
      });
    });
    // load patterns for this doctor
    apiGet<{ patterns: any[] }>(`/api/doctors/${selectedId}/patterns`).then(r => setPatterns(r.patterns || [])).catch(() => {});
  }, [selectedId]);

  async function save() {
    setSaving(true); setMsg("");
    try {
      await apiPatch(`/api/doctors/${selectedId}/profile`, form);
      setProfile({ ...form }); setEditing(false);
      setMsg("✅ 风格配置已保存");
    } catch (e: any) { setMsg(`❌ 保存失败：${e.message}`); }
    finally { setSaving(false); }
  }

  // V1.2 §6: generate QR token
  async function generateQR() {
    setMsg("");
    try {
      const r = await apiPost<{ token: string; full_url: string }>(
        `/api/doctors/${selectedId}/qr-token`, {});
      setQrToken(r.token); setQrUrl(r.full_url);
    } catch (e: any) { setMsg(`QR 生成失败: ${e.message}`); }
  }

  // V1.2 §5: batch case import (text or images)
  async function importCases() {
    setMsg("");
    try {
      let r: any;
      if (importMode === "images") {
        if (caseImages.length === 0) { setMsg("请选择至少一张病例图片"); return; }
        const fd = new FormData();
        for (const f of caseImages) fd.append("files", f);
        r = await apiUpload<{ import_id: string; total: number }>(
          `/api/doctors/${selectedId}/cases/import-images`, fd);
        setImportId(r.import_id);
        setMsg(`✅ 已提交 ${r.total} 张病例图片，后台 OCR + 结构化分析中…`);
      } else {
        const lines = caseText.split(/\n\s*\n/).filter(s => s.trim());
        const cases = lines.map((text, i) => ({ id: `case-${i}`, text }));
        if (cases.length === 0) { setMsg("请粘贴至少一份病例文本"); return; }
        r = await apiPost<{ import_id: string; total: number }>(
          `/api/doctors/${selectedId}/cases/import`, { cases });
        setImportId(r.import_id);
        setMsg(`✅ 已提交 ${r.total} 份病例文本，后台分析中…`);
      }
      pollImport();
    } catch (e: any) { setMsg(`导入失败: ${e.message}`); }
  }

  function onImageSelect(files: FileList | null) {
    if (!files) return;
    const arr = Array.from(files).filter(f => f.type.startsWith("image/"));
    setCaseImages(prev => [...prev, ...arr]);
  }

  function removeImage(idx: number) {
    setCaseImages(prev => prev.filter((_, i) => i !== idx));
  }

  async function pollImport() {
    if (!importId) return;
    const interval = setInterval(async () => {
      try {
        const r = await apiGet<any>(`/api/doctors/${selectedId}/case-imports/${importId}`);
        setImportProgress(r);
        if (r.status === "done" || r.status === "failed") {
          clearInterval(interval);
          // refresh patterns
          const pr = await apiGet<{ patterns: any[] }>(`/api/doctors/${selectedId}/patterns`);
          setPatterns(pr.patterns || []);
        }
      } catch { clearInterval(interval); }
    }, 3000);
  }

  async function confirmPattern(pid: string) {
    try {
      await apiPost(`/api/doctors/${selectedId}/patterns/${pid}/confirm`, {});
      setMsg("✅ 已采纳此经验规律，写入医生画像");
      const pr = await apiGet<{ patterns: any[] }>(`/api/doctors/${selectedId}/patterns`);
      setPatterns(pr.patterns || []);
    } catch (e: any) { setMsg(`确认失败: ${e.message}`); }
  }

  const selectedDoctor = doctors.find((d) => d.id === selectedId);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">医师个性化配置</h1>
        <Link href="/encounters/enc-003" className="btn-ghost text-xs">→ 到诊疗室对比效果</Link>
      </div>

      <div className="card text-sm text-slate-600">
        <p>
          每位医师可配置独立的辨证偏好、方药偏好和分析重点。AI 分析时将据此调整输出风格。
        </p>
      </div>

      {/* 医师选择 */}
      <div className="flex flex-wrap gap-2">
        {doctors.map((d) => (
          <button key={d.id} onClick={() => setSelectedId(d.id)}
            className={`px-3 py-1.5 rounded text-sm transition ${
              selectedId === d.id ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"
            }`}>
            {d.id} {d.name}
            {d.dept && <span className="text-xs ml-1 opacity-70">({d.dept})</span>}
          </button>
        ))}
      </div>

      {msg && (
        <div className={`text-sm px-3 py-2 rounded ${msg.startsWith("✅") ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"}`}>
          {msg}
        </div>
      )}

      {/* V1.2 §6: QR code section */}
      {selectedId && (
        <div className="card border-l-4 border-l-brand-500">
          <h2 className="font-semibold mb-2 text-brand-700">专属问诊二维码（V1.2 §6）</h2>
          <p className="text-xs text-slate-500 mb-2">
            患者扫码 → 手机问诊 → 自动绑定此医生。二维码仅含路由+令牌，不泄露医生信息。
          </p>
          {!qrUrl ? (
            <button onClick={generateQR} className="btn-primary text-xs">生成专属二维码</button>
          ) : (
            <div className="flex gap-4 items-start">
              <QRCodeImage text={qrUrl} size={200} />
              <div className="text-sm space-y-1">
                <div className="font-medium">访问地址：</div>
                <div className="text-xs text-slate-600 font-mono break-all">{qrUrl}</div>
                <div className="text-xs text-slate-400 mt-2">Token: {qrToken}</div>
                <div className="text-xs text-green-600 mt-2">
                  ✅ 患者扫码后将自动绑定 <b>{selectedDoctor?.name}</b>
                </div>
                <button onClick={generateQR} className="btn-ghost text-xs mt-2">重新生成</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* V1.2 §5: Case mining section */}
      {selectedId && (
        <div className="card border-l-4 border-l-brand-500 space-y-3">
          <h2 className="font-semibold mb-1 text-brand-700">经验挖掘（V1.2 §5）</h2>
          <p className="text-xs text-slate-500">
            批量上传历史病例 → AI OCR+结构化 → 相似聚类 → 归纳辨证/方药规律 → 医生确认后写入画像。
            支持图片上传（自动 OCR）或文本粘贴。先 10-20 份验证质量，确认后可扩展到 ~100 份。
          </p>

          {/* Mode switch */}
          <div className="flex gap-2">
            <button onClick={() => setImportMode("images")}
              className={`px-3 py-1 rounded text-xs ${importMode === "images" ? "bg-brand-600 text-white" : "bg-slate-100"}`}>
              📷 图片上传（OCR）
            </button>
            <button onClick={() => setImportMode("text")}
              className={`px-3 py-1 rounded text-xs ${importMode === "text" ? "bg-brand-600 text-white" : "bg-slate-100"}`}>
              📝 文本粘贴
            </button>
          </div>

          {importMode === "images" ? (
            <div className="space-y-2">
              <input ref={imgRef} type="file" accept="image/*" multiple
                className="hidden"
                onChange={e => onImageSelect(e.target.files)} />
              <button onClick={() => imgRef.current?.click()}
                className="btn-ghost text-xs">
                + 选择病例图片（可多选）
              </button>
              {caseImages.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {caseImages.map((f, i) => (
                    <div key={i} className="relative">
                      <img src={URL.createObjectURL(f)} alt={f.name}
                        className="w-20 h-20 object-cover rounded border" />
                      <button onClick={() => removeImage(i)}
                        className="absolute -top-1 -right-1 bg-red-500 text-white rounded-full w-5 h-5 text-xs flex items-center justify-center">
                        ✕
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <div className="text-xs text-slate-400">
                已选 {caseImages.length} 张图片，每张图片为一份病例，系统将自动 OCR 识别文字后结构化分析
              </div>
            </div>
          ) : (
            <textarea value={caseText} onChange={e => setCaseText(e.target.value)}
              className="w-full border rounded p-2 text-sm min-h-24"
              placeholder="粘贴病例文本，用空行分隔每份病例。每份可包含主诉、症状、辨证、方药、疗效等。" />
          )}

          <div className="flex items-center gap-2">
            <button onClick={importCases}
              disabled={importMode === "images" ? caseImages.length === 0 : !caseText.trim()}
              className="btn-primary text-xs">
              {importMode === "images" ? "上传图片并分析" : "批量导入并分析"}
            </button>
            {importProgress && (
              <span className="text-xs text-slate-500">
                状态：{importProgress.status} · 进度：{importProgress.processed_count}/{importProgress.total_count}
              </span>
            )}
          </div>
          {importProgress?.status === "done" && (
            <div className="text-xs text-green-600">✅ 分析完成，{patterns.length} 条规律已归纳</div>
          )}
          {importProgress?.status === "failed" && (
            <div className="text-xs text-red-600">❌ 分析失败：{importProgress.error}</div>
          )}
        </div>
      )}

      {/* Experience patterns */}
      {patterns.length > 0 && (
        <div className="card space-y-2">
          <h2 className="font-semibold text-brand-700">挖掘出的经验规律</h2>
          {patterns.map((p, i) => (
            <div key={i} className="border rounded p-3 text-sm">
              <div className="flex items-center justify-between">
                <span className="badge-muted">{p.category}</span>
                <span className="text-xs text-slate-400">样本量: {p.sample_size}</span>
              </div>
              <div className="mt-1 text-slate-700">{p.pattern_text}</div>
              {p.conditions && <div className="text-xs text-slate-500 mt-1">适用条件：{p.conditions}</div>}
              {p.exceptions && <div className="text-xs text-slate-500">例外：{p.exceptions}</div>}
              {p.source_cases?.length > 0 && (
                <div className="text-xs text-slate-400 mt-1">来源病例：{p.source_cases.join("、")}</div>
              )}
              <div className="text-xs mt-1">置信度: {(p.confidence * 100).toFixed(0)}%</div>
              {p.confirmed ? (
                <span className="text-xs text-green-600 mt-1 inline-block">✅ 已采纳</span>
              ) : (
                <button onClick={() => confirmPattern(p.id)}
                  className="btn-ghost text-xs mt-1">确认采纳 → 写入画像</button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 风格详情/编辑 */}
      {profile !== null || selectedId ? (
        <div className="card space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">{selectedDoctor?.name || selectedId} 的诊疗风格</h2>
            {!editing ? (
              <button onClick={() => { setEditing(true); setForm(profile || form); }} className="btn-primary text-xs">
                编辑风格
              </button>
            ) : (
              <div className="flex gap-2">
                <button onClick={save} disabled={saving} className="btn-primary text-xs">
                  {saving ? "保存中…" : "保存"}
                </button>
                <button onClick={() => { setEditing(false); setForm(profile || form); }} className="btn-ghost text-xs">
                  取消
                </button>
              </div>
            )}
          </div>
          {!editing ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
              <Field label="科室" value={profile?.dept} />
              <Field label="擅长领域" value={(profile?.specialties || []).join("、")} />
              <Field label="辨证偏好" value={profile?.syndrome_preference} multiline />
              <Field label="方剂偏好" value={profile?.formula_preference} multiline />
              <Field label="用药偏好" value={profile?.herb_preference} multiline />
              <Field label="非药物治疗偏好" value={profile?.non_drug_preference} multiline />
              <Field label="分析重点" value={profile?.analysis_focus} multiline />
              <Field label="经验笔记" value={(profile?.experience_notes || []).join("\n")} multiline />
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
              <EditField label="科室" value={form.dept} onChange={(v) => setForm({ ...form, dept: v })} />
              <EditField label="擅长领域（顿号分隔）" value={(form.specialties || []).join("、")}
                onChange={(v) => setForm({ ...form, specialties: v.split(/[、,，]/).map(s=>s.trim()).filter(Boolean) })} />
              <EditField label="辨证偏好" value={form.syndrome_preference} multiline
                onChange={(v) => setForm({ ...form, syndrome_preference: v })}
                hint="如：重视气阴两虚、注重脾胃升降" />
              <EditField label="方剂偏好" value={form.formula_preference} multiline
                onChange={(v) => setForm({ ...form, formula_preference: v })}
                hint="如：喜用生脉散合玉泉丸、慎用大寒之品" />
              <EditField label="用药偏好" value={form.herb_preference} multiline
                onChange={(v) => setForm({ ...form, herb_preference: v })}
                hint="如：黄芪常用30g起步、五味子不超过6g" />
              <EditField label="非药物治疗偏好" value={form.non_drug_preference} multiline
                onChange={(v) => setForm({ ...form, non_drug_preference: v })}
                hint="如：推拿优先、配合艾灸足三里" />
              <EditField label="分析重点" value={form.analysis_focus} multiline
                onChange={(v) => setForm({ ...form, analysis_focus: v })}
                hint="如：侧重并发症筛查、关注糖代谢指标" />
              <EditField label="经验笔记（换行分隔）" value={(form.experience_notes || []).join("\n")} multiline
                onChange={(v) => setForm({ ...form, experience_notes: v.split("\n").map(s=>s.trim()).filter(Boolean) })} />
            </div>
          )}
        </div>
      ) : (
        <div className="card text-sm text-slate-400">请选择一位医师</div>
      )}
    </div>
  );
}

function Field({ label, value, multiline }: { label: string; value?: string; multiline?: boolean }) {
  return (
    <div>
      <div className="text-xs font-semibold text-slate-500 uppercase mb-1">{label}</div>
      {multiline ? (
        <div className="whitespace-pre-wrap text-slate-700 min-h-[2rem]">{value || "—"}</div>
      ) : (
        <div className="text-slate-700">{value || "—"}</div>
      )}
    </div>
  );
}

function EditField({
  label, value, onChange, multiline, hint,
}: {
  label: string; value: string; onChange: (v: string) => void; multiline?: boolean; hint?: string;
}) {
  return (
    <div>
      <label className="text-xs font-semibold text-slate-500 uppercase mb-1 block">{label}</label>
      {multiline ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border rounded px-2 py-1.5 text-sm min-h-[3rem]"
          placeholder={hint || ""}
        />
      ) : (
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full border rounded px-2 py-1.5 text-sm"
          placeholder={hint || ""}
        />
      )}
      {hint && !multiline && (
        <div className="text-[10px] text-slate-400 mt-0.5">{hint}</div>
      )}
    </div>
  );
}
