"use client";

import { useState, useRef } from "react";
import { API_BASE } from "@/lib/api";

// LabUpload — upload lab report image → OCR → display text → allow editing
// V1.0: updatev1_1.md 入口A patient can upload lab reports, 入口B doctor too
// updatev2_1 §2: structured error classification + request_id display

const OCR_ERROR_CATEGORIES: Record<string, string> = {
  upload_failed: "图片上传失败",
  ocr_unavailable: "OCR 服务不可用",
  timeout: "OCR 推理超时",
  model_unavailable: "OCR 模型未启动",
  unknown: "OCR 不可用",
};

function parseOcrError(data: any, httpStatus: number): string {
  const detail = typeof data?.detail === "string" ? data.detail : data?.detail?.detail || "";
  const rid = (typeof data?.detail === "object" ? data?.detail?.request_id : data?.request_id) || "";
  const cat = (typeof data?.detail === "object" ? data?.detail?.category : data?.category) || "";
  const label = OCR_ERROR_CATEGORIES[cat] || `OCR 不可用 (${httpStatus})`;
  const detailPart = detail ? `：${detail}` : "";
  return rid ? `${label}${detailPart} [${rid}]` : `${label}${detailPart}`;
}

export default function LabUpload({
  onParsed,
}: {
  onParsed: (labs: any[], rawText: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [ocrText, setOcrText] = useState("");
  const [err, setErr] = useState("");
  const [preview, setPreview] = useState("");
  const [source, setSource] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function uploadLab(file: File) {
    setBusy(true);
    setErr("");
    setOcrText("");
    setSource("");
    setPreview(URL.createObjectURL(file));
    try {
      const formData = new FormData();
      formData.append("file", file);
      const r = await fetch(`${API_BASE}/api/ocr/extract`, {
        method: "POST",
        body: formData,
      });
      const txt = await r.text();
      let data: any = null;
      try { data = txt ? JSON.parse(txt) : null; } catch {}
      if (!r.ok) {
        setErr(parseOcrError(data, r.status));
        return;
      }
      const text = data?.text || "";
      setOcrText(text);
      setSource(data?.source || "");
      const labs = parseLabs(text);
      onParsed(labs, text);
    } catch (e: any) {
      setErr(`OCR 失败: ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  // Parse OCR text into structured lab items (item, value, unit, ref, flag)
  function parseLabs(text: string): any[] {
    const labs: any[] = [];
    const lines = text.split("\n");
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      // Match patterns like: 项目名  数值  单位  参考范围  标志
      // e.g. "血糖  8.5  mmol/L  3.9-6.1  ↑"
      const m = trimmed.match(/^(.+?)\s{1,}(\d+\.?\d*)\s{1,}([a-zA-Z%/μµ·]+)?\s*(?:\(([^)]+)\))?/);
      if (m) {
        labs.push({
          item: m[1].trim(),
          value: m[2],
          unit: m[3] || "",
          ref: m[4] || "",
          flag: trimmed.includes("↑") || trimmed.includes("高") ? "HIGH" :
                trimmed.includes("↓") || trimmed.includes("低") ? "LOW" : "",
        });
      }
    }
    return labs;
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) uploadLab(f);
          }}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
          className="btn-ghost text-xs"
        >
          {busy ? "⏳ OCR 识别中…（约 30-60s）" : "📄 上传检验报告"}
        </button>
        {preview && (
          <a href={preview} target="_blank" className="text-xs text-brand-600 hover:underline">
            查看原图
          </a>
        )}
        {source && !busy && (
          <span className="text-[10px] text-slate-400">
            识别引擎：{source === "clinical_main_vlm" ? "VLM" : source}
          </span>
        )}
      </div>
      {err && <div className="text-xs text-red-500">{err}</div>}
      {ocrText && (
        <div className="border rounded p-2 bg-slate-50">
          <div className="text-xs text-slate-500 mb-1">OCR 识别结果（可编辑）：</div>
          <textarea
            value={ocrText}
            onChange={(e) => {
              setOcrText(e.target.value);
              onParsed(parseLabs(e.target.value), e.target.value);
            }}
            className="w-full border rounded p-2 text-xs min-h-24 bg-white"
          />
        </div>
      )}
    </div>
  );
}
