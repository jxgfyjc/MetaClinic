"use client";
import { useState } from "react";
import { Maximize2, Minus, Plus, RotateCw } from "lucide-react";

/** Zoomable / scrollable image — used to check OCR values against the ORIGINAL report image. */
export function ImageViewer({ src, alt = "", height = 420 }: { src: string; alt?: string; height?: number }) {
  const [z, setZ] = useState(1); const [rot, setRot] = useState(0); const [err, setErr] = useState(false);
  return (
    <div className="overflow-hidden rounded-xl border border-ink-100 bg-ink-50">
      <div className="flex items-center gap-1 border-b border-ink-100 bg-white px-2 py-1.5 text-ink-500">
        <button type="button" className="btn-ghost btn-sm" onClick={() => setZ((v) => Math.max(0.5, +(v - 0.25).toFixed(2)))} aria-label="缩小"><Minus size={14} /></button>
        <span className="w-12 text-center text-xs tabular-nums">{Math.round(z * 100)}%</span>
        <button type="button" className="btn-ghost btn-sm" onClick={() => setZ((v) => Math.min(4, +(v + 0.25).toFixed(2)))} aria-label="放大"><Plus size={14} /></button>
        <button type="button" className="btn-ghost btn-sm" onClick={() => { setZ(1); setRot(0); }} aria-label="适应窗口"><Maximize2 size={14} /></button>
        <button type="button" className="btn-ghost btn-sm" onClick={() => setRot((r) => (r + 90) % 360)} aria-label="旋转"><RotateCw size={14} /></button>
        <a href={src} target="_blank" rel="noreferrer" className="ml-auto text-xs text-brand-600 hover:underline">新窗口打开原图</a>
      </div>
      <div className="overflow-auto" style={{ height }}>
        {err ? <div className="grid h-full place-items-center text-sm text-danger-600">图片加载失败（文件可能已被清理）</div> :
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt={alt} onError={() => setErr(true)} style={{ width: `${z * 100}%`, maxWidth: "none", transform: rot ? `rotate(${rot}deg)` : undefined, transformOrigin: "center" }} className="block" />}
      </div>
    </div>
  );
}
