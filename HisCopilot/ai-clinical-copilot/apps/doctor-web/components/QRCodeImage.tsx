"use client";

import { useEffect, useState } from "react";
import QRCode from "qrcode";

/**
 * QRCode — V1.2 §6: doctor QR code for patient intake.
 * Uses the `qrcode` npm package for reliable, spec-compliant QR generation.
 * Private deployment — does NOT depend on any external QR API.
 */

export default function QRCodeImage({
  text,
  size = 200,
}: {
  text: string;
  size?: number;
}) {
  const [dataUrl, setDataUrl] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    if (!text) { setDataUrl(""); return; }
    QRCode.toDataURL(text, {
      width: size,
      margin: 1,
      errorCorrectionLevel: "M",
    }).then(setDataUrl).catch(e => setErr(e.message));
  }, [text, size]);

  if (err) {
    return (
      <div className="text-xs text-red-500 p-2 border rounded">
        QR 生成失败：{err}
      </div>
    );
  }

  if (!dataUrl) {
    return (
      <div
        className="flex items-center justify-center bg-slate-100 rounded"
        style={{ width: size, height: size }}
      >
        <span className="text-xs text-slate-400">生成中…</span>
      </div>
    );
  }

  return (
    <img
      src={dataUrl}
      alt="QR Code"
      width={size}
      height={size}
      style={{ borderRadius: 4 }}
    />
  );
}
