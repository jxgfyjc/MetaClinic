"use client";
import { useEffect, useState } from "react";
import QRCode from "qrcode";

/** Locally generated QR (no external service — patient links never leave the private network). */
export function QrBox({ text, size = 200 }: { text: string; size?: number }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let live = true;
    QRCode.toDataURL(text, { width: size * 2, margin: 1, errorCorrectionLevel: "M" }).then((u) => live && setSrc(u)).catch(() => live && setSrc(""));
    return () => { live = false; };
  }, [text, size]);
  return src ? <img src={src} width={size} height={size} alt="二维码" className="rounded-md bg-white" /> : <div style={{ width: size, height: size }} className="skeleton" />;
}
