"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Mic, Square, Upload } from "lucide-react";
import { Spinner } from "./ui";

/**
 * Voice capture that works in every environment and never lies about what it can do.
 *
 *  - Secure context (HTTPS / localhost) + MediaRecorder available → live recording with a level meter.
 *  - Otherwise (e.g. phone over plain HTTP: `navigator.mediaDevices` is undefined) → a file input with
 *    `capture`, which makes mobile browsers open the NATIVE voice recorder; the user records there and the
 *    file is returned to us. The UI says so explicitly instead of showing a dead mic button.
 *
 * The component only produces a File and hands it to `onFile`; upload/ASR status is owned by the caller so the same
 * status UI (queued → transcribing → text / failed + retry) is used everywhere.
 */
export function VoiceCapture({ onFile, busy, disabled, label = "语音录入" }: { onFile: (f: File) => void | Promise<void>; busy?: boolean; disabled?: boolean; label?: string }) {
  const [canRecord, setCanRecord] = useState<boolean | null>(null);
  const [rec, setRec] = useState(false); const [secs, setSecs] = useState(0); const [level, setLevel] = useState(0); const [err, setErr] = useState("");
  const mr = useRef<MediaRecorder | null>(null); const chunks = useRef<Blob[]>([]); const stream = useRef<MediaStream | null>(null);
  const raf = useRef(0); const timer = useRef<any>(null); const actx = useRef<AudioContext | null>(null); const startedAt = useRef(0);

  useEffect(() => { setCanRecord(typeof window !== "undefined" && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined"); }, []);
  const cleanup = useCallback(() => {
    cancelAnimationFrame(raf.current); clearInterval(timer.current);
    stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null;
    actx.current?.close().catch(() => {}); actx.current = null;
  }, []);
  useEffect(() => cleanup, [cleanup]);

  const start = async () => {
    setErr("");
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      stream.current = s;
      const AC = (window as any).AudioContext || (window as any).webkitAudioContext;
      if (AC) {
        const ctx = new AC(); actx.current = ctx; const an = ctx.createAnalyser(); an.fftSize = 256; ctx.createMediaStreamSource(s).connect(an);
        const buf = new Uint8Array(an.frequencyBinCount);
        const tick = () => { an.getByteTimeDomainData(buf); let sum = 0; for (const v of buf) { const x = (v - 128) / 128; sum += x * x; } setLevel(Math.min(1, Math.sqrt(sum / buf.length) * 3.2)); raf.current = requestAnimationFrame(tick); };
        tick();
      }
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
      const r = new MediaRecorder(s, mime ? { mimeType: mime } : undefined); mr.current = r; chunks.current = [];
      r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      r.onstop = async () => {
        const dur = (Date.now() - startedAt.current) / 1000; cleanup(); setRec(false); setLevel(0);
        const type = r.mimeType || "audio/webm"; const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
        const blob = new Blob(chunks.current, { type });
        if (dur < 0.8 || blob.size < 1500) { setErr("录音过短，请按住说话至少 1 秒后再停止"); return; }
        await onFile(new File([blob], `rec-${Date.now()}.${ext}`, { type }));
      };
      startedAt.current = Date.now(); setSecs(0); timer.current = setInterval(() => setSecs(Math.floor((Date.now() - startedAt.current) / 1000)), 250);
      r.start(250); setRec(true);
    } catch (e: any) {
      cleanup();
      setErr(e?.name === "NotAllowedError" ? "麦克风权限被拒绝：请在浏览器地址栏左侧的权限设置中允许麦克风" : e?.name === "NotFoundError" ? "未检测到麦克风设备" : `无法使用麦克风：${e?.message || e?.name || "未知错误"}`);
    }
  };
  const stop = () => { if (mr.current && mr.current.state !== "inactive") mr.current.stop(); };

  if (canRecord === null) return <div className="h-9 w-32 skeleton" />;
  if (!canRecord) {
    return (
      <div className="space-y-1.5">
        <label className={`btn-secondary cursor-pointer ${busy || disabled ? "pointer-events-none opacity-50" : ""}`}>
          {busy ? <Spinner /> : <Mic size={15} />}<span>{label}（打开手机录音）</span>
          <input type="file" accept="audio/*" capture className="hidden" disabled={busy || disabled} onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
        </label>
        <div className="hint">当前网址为非加密连接，浏览器不允许网页直接录音。点击后将调用手机自带录音机，录完选择该文件即可。{" "}
          <label className="cursor-pointer text-brand-600 underline"><Upload size={11} className="mr-0.5 inline" />改为选择已有音频<input type="file" accept="audio/*" className="hidden" disabled={busy || disabled} onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} /></label></div>
        {err && <div className="text-xs text-danger-600">{err}</div>}
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2.5">
        {!rec ? <button type="button" className="btn-secondary" onClick={start} disabled={busy || disabled}>{busy ? <Spinner /> : <Mic size={15} />}{label}</button>
          : <button type="button" className="btn-danger" onClick={stop}><Square size={13} fill="currentColor" />停止并识别 · {String(Math.floor(secs / 60)).padStart(2, "0")}:{String(secs % 60).padStart(2, "0")}</button>}
        {rec && <div className="flex items-center gap-2" aria-label="音量"><div className="h-2 w-24 overflow-hidden rounded-full bg-ink-100"><div className="h-full rounded-full bg-jade-500 transition-[width] duration-75" style={{ width: `${Math.round(level * 100)}%` }} /></div><span className="text-xs text-ink-400">{level < 0.04 ? "没有检测到声音，请靠近麦克风" : "录音中"}</span></div>}
        {!rec && <label className="cursor-pointer text-xs text-brand-600 underline"><input type="file" accept="audio/*" className="hidden" disabled={busy || disabled} onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />上传音频文件</label>}
      </div>
      {err && <div className="text-xs text-danger-600">{err}</div>}
    </div>
  );
}
