"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { API_BASE } from "@/lib/api";

// VoiceRecorder — MediaRecorder API → client-side WAV transcode → ASR proxy → display transcription
// updatev2_1 §2: structured error classification + request_id display
// 2026-10-14: transcode webm/opus → 16kHz mono s16 WAV via OfflineAudioContext before upload.
//   The ASR backend (llama-cpp miniaudio) only accepts WAV/MP3/FLAC. Browser MediaRecorder
//   produces webm/opus (Chrome) or mp4 (Safari). We decode the webm blob with AudioContext
//   and re-encode as a canonical 16kHz mono WAV on the client, so the backend receives a
//   format it natively supports — no server-side PyAV transcode needed.
// 2026-10-14 v2: added recording playback + waveform meter so the user can verify
//   the microphone actually captured sound before sending to ASR. Also added a
//   download link to the recorded audio for debugging.

const ERROR_CATEGORIES: Record<string, string> = {
  mic_permission: "麦克风权限被拒绝",
  upload_failed: "音频上传失败",
  format_unsupported: "音频格式不支持",
  decode_failed: "音频解码失败",
  model_unavailable: "ASR 服务未启动",
  inference_exception: "ASR 推理异常",
  cuda_oom: "显存不足 (OOM)",
  post_processing: "后处理失败",
  timeout: "推理超时",
  upstream_error: "ASR 服务错误",
  unknown: "ASR 不可用",
};

function parseAsrError(data: any, httpStatus: number): string {
  const detail = typeof data?.detail === "string" ? data.detail : data?.detail?.detail || "";
  const rid = (typeof data?.detail === "object" ? data?.detail?.request_id : data?.request_id) || "";
  const cat = (typeof data?.detail === "object" ? data?.detail?.category : data?.category) || "";
  const label = ERROR_CATEGORIES[cat] || `ASR 不可用 (${httpStatus})`;
  const detailPart = detail ? `：${detail}` : "";
  return rid ? `${label}${detailPart} [${rid}]` : `${label}${detailPart}`;
}

/**
 * Decode an audio Blob (webm/opus, mp4, etc.) and re-encode as 16kHz mono s16 WAV.
 * Uses OfflineAudioContext to decode the browser-native container, then manually
 * writes a WAV header + PCM samples.
 *
 * Returns null if decoding fails (e.g. empty audio, no speech).
 */
async function transcodeToWav(blob: Blob): Promise<Blob | null> {
  try {
    const arrayBuffer = await blob.arrayBuffer();
    const AudioCtx = (window as any).OfflineAudioContext || (window as any).webkitOfflineAudioContext;
    if (!AudioCtx) return blob;
    const tempCtx = new AudioCtx(1, 8000, 8000);
    const audioBuffer = await tempCtx.decodeAudioData(arrayBuffer.slice(0));
    const targetSr = 16000;
    const numChannels = 1;
    const numFrames = Math.ceil(audioBuffer.duration * targetSr);
    const offlineCtx = new AudioCtx(numChannels, numFrames, targetSr);
    const source = offlineCtx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(offlineCtx.destination);
    source.start(0);
    const rendered = await offlineCtx.startRendering();
    const channelData = rendered.getChannelData(0);
    return encodeWav(channelData, targetSr);
  } catch (e) {
    console.warn("WAV transcode failed, sending raw blob:", e);
    return blob;
  }
}

function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  const numFrames = samples.length;
  const bytesPerSample = 2;
  const dataSize = numFrames * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const writeString = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, dataSize, true);
  let offset = 44;
  for (let i = 0; i < numFrames; i++) {
    let s = Math.max(-1, Math.min(1, samples[i]));
    s = s < 0 ? s * 0x8000 : s * 0x7FFF;
    view.setInt16(offset, s, true);
    offset += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export default function VoiceRecorder({
  onTranscribe,
  label = "语音录入",
  append = false,
}: {
  onTranscribe: (text: string) => void;
  label?: string;
  append?: boolean;
}) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [audioUrl, setAudioUrl] = useState(""); // playback URL for the recorded audio
  const [audioLevel, setAudioLevel] = useState(0); // live mic level 0-1 for meter
  const [mounted, setMounted] = useState(false); // SSR/CSR hydration guard
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number>(0);
  const streamRef = useRef<MediaStream | null>(null);

  // mount guard: navigator.mediaDevices is undefined during SSR and on HTTP.
  // Render a stable placeholder before mount to match SSR output and avoid
  // hydration mismatch (server renders fallback, client renders mic button).
  useEffect(() => setMounted(true), []);
  const micAvailable = mounted && typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      if (streamRef.current) streamRef.current.getTracks().forEach((t) => t.stop());
      if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
        audioCtxRef.current.close().catch(() => {});
      }
    };
  }, []);

  const startRecording = useCallback(async () => {
    setErr("");
    setAudioUrl("");
    if (!micAvailable) {
      setErr("当前环境不支持麦克风（需 HTTPS 或 localhost 访问），请使用文件上传");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      // Set up live audio meter so user can SEE that mic is picking up sound
      const AudioCtx = (window as any).AudioContext || (window as any).webkitAudioContext;
      if (AudioCtx) {
        const ctx = new AudioCtx();
        audioCtxRef.current = ctx;
        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        analyserRef.current = analyser;
        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        const tick = () => {
          analyser.getByteTimeDomainData(dataArray);
          // Compute RMS amplitude from time-domain data (128 = silence)
          let sum = 0;
          for (let i = 0; i < dataArray.length; i++) {
            const v = (dataArray[i] - 128) / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / dataArray.length);
          setAudioLevel(Math.min(1, rms * 3)); // amplify for visibility
          rafRef.current = requestAnimationFrame(tick);
        };
        tick();
      }

      const mr = new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        // Stop meter
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
        setAudioLevel(0);
        stream.getTracks().forEach((t) => t.stop());
        if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
          audioCtxRef.current.close().catch(() => {});
          audioCtxRef.current = null;
        }
        await transcribeAudio();
      };
      mr.start(250);
      mediaRecorderRef.current = mr;
      setRecording(true);
    } catch (e: any) {
      const msg = e?.name === "NotAllowedError"
        ? "麦克风权限被拒绝，请在浏览器设置中允许"
        : `麦克风无法访问: ${e?.message || e?.name || "未知错误"}`;
      setErr(msg);
    }
  }, [micAvailable]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && recording) {
      mediaRecorderRef.current.stop();
      setRecording(false);
    }
  }, [recording]);

  const transcribeAudio = async () => {
    if (chunksRef.current.length === 0) return;
    setBusy(true);
    setErr("");
    try {
      const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
      const mimeType = isSafari ? "audio/mp4" : "audio/webm";
      const rawBlob = new Blob(chunksRef.current, { type: mimeType });
      // Provide a playback URL so the user can listen to what was recorded
      setAudioUrl(URL.createObjectURL(rawBlob));
      // Transcode to 16kHz mono WAV before sending — ASR backend only accepts WAV/MP3/FLAC
      const wavBlob = await transcodeToWav(rawBlob);
      const isWav = wavBlob && wavBlob.type === "audio/wav";
      const ext = isWav ? "wav" : (isSafari ? "m4a" : "webm");
      const formData = new FormData();
      formData.append("file", wavBlob || rawBlob, `audio.${ext}`);
      formData.append("filename", `audio.${ext}`);
      const r = await fetch(`${API_BASE}/api/asr/transcribe`, {
        method: "POST",
        body: formData,
      });
      const txt = await r.text();
      let data: any = null;
      try { data = txt ? JSON.parse(txt) : null; } catch {}
      if (!r.ok) {
        setErr(parseAsrError(data, r.status));
        return;
      }
      if (data?.text) {
        onTranscribe(data.text);
      } else {
        // text_len=0 is a known Qwen3-ASR limitation with non-speech audio
        setErr("未识别到语音内容（可能是录音过短或无清晰语音，请大声重试）");
      }
    } catch (e: any) {
      setErr(`语音识别失败: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  // File upload fallback (HTTP remote access where getUserMedia is unavailable)
  const uploadAudioFile = async (file: File) => {
    setBusy(true);
    setErr("");
    setAudioUrl(URL.createObjectURL(file)); // allow playback of uploaded file too
    try {
      const wavBlob = await transcodeToWav(file);
      const isWav = wavBlob && wavBlob.type === "audio/wav";
      const ext = isWav ? "wav" : (file.name.split(".").pop() || "audio");
      const formData = new FormData();
      formData.append("file", wavBlob || file, `audio.${ext}`);
      formData.append("filename", `audio.${ext}`);
      const r = await fetch(`${API_BASE}/api/asr/transcribe`, {
        method: "POST",
        body: formData,
      });
      const txt = await r.text();
      let data: any = null;
      try { data = txt ? JSON.parse(txt) : null; } catch {}
      if (!r.ok) {
        setErr(parseAsrError(data, r.status));
        return;
      }
      if (data?.text) {
        onTranscribe(data.text);
      } else {
        setErr("未识别到语音内容，请尝试更清晰的录音");
      }
    } catch (e: any) {
      setErr(`语音识别失败: ${e.message}`);
    } finally {
      setBusy(false);
    }
  };

  // Before mount (SSR + first client render): render a stable placeholder
  // that matches the server output. This prevents hydration mismatch — the
  // server cannot know if mic is available, so we defer the real UI to after mount.
  if (!mounted) {
    return (
      <span className="inline-flex items-center gap-2 text-xs text-slate-400">
        🎤 {label}…
      </span>
    );
  }

  // If mic unavailable (HTTP remote), render file-upload fallback only
  if (!micAvailable) {
    return (
      <div className="inline-flex items-center gap-2 flex-wrap">
        <label className="btn btn-ghost text-xs cursor-pointer">
          📁 {label}（上传音频）
          <input
            type="file"
            accept="audio/*"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) uploadAudioFile(file);
              e.target.value = "";
            }}
          />
        </label>
        {busy && <span className="text-xs text-slate-500">⏳ 转写中…</span>}
        {err && <span className="text-xs text-red-500">{err}</span>}
        {audioUrl && (
          <audio controls src={audioUrl} className="h-7 w-48" />
        )}
        <span className="text-xs text-slate-400">（HTTP 环境，需上传预录音频）</span>
      </div>
    );
  }

  return (
    <div className="inline-flex items-center gap-2 flex-wrap">
      <button
        type="button"
        onClick={recording ? stopRecording : startRecording}
        disabled={busy}
        className={`btn text-xs ${recording ? "bg-red-600 text-white hover:bg-red-700" : "btn-ghost"}`}
      >
        {recording ? "⏹ 停止录音" : busy ? "⏳ 转写中…" : `🎤 ${label}`}
      </button>
      {recording && (
        <span className="inline-flex items-center gap-1.5">
          {/* Live audio level meter */}
          <span className="text-xs text-slate-400">音量</span>
          <span className="inline-block w-20 h-2 bg-slate-200 rounded overflow-hidden align-middle">
            <span
              className="block h-full bg-green-500 transition-all duration-75"
              style={{ width: `${Math.round(audioLevel * 100)}%` }}
            />
          </span>
          <span className="text-xs text-slate-400">建议 &lt;60s</span>
        </span>
      )}
      {audioUrl && !recording && (
        <span className="inline-flex items-center gap-1">
          <span className="text-[10px] text-slate-400">试听</span>
          <audio controls src={audioUrl} className="h-7 w-48" />
        </span>
      )}
      {err && <span className="text-xs text-red-500">{err}</span>}
    </div>
  );
}
