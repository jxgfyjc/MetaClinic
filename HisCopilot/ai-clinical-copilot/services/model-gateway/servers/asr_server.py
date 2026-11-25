"""ASR server — Qwen3-ASR-1.7B gguf + mmproj via llama-cpp-python.

OpenAI-compatible `/v1/audio/transcriptions` on GPU1. instruct #72.
updatev2_1.md §2: full diagnostics — request_id, stage timing, error
classification, audio metadata, structured logging, smoke test endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
import tempfile
import base64
import logging
import traceback
import uuid
import io as _io
import wave as _wave
import math
import struct
from enum import Enum
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger("asr_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")


def effective_threads(requested: int | None = None) -> int:
    """CPU threads for llama.cpp. The container has a CFS quota (cpu.max) far
    below nproc (e.g. 27 vs 128 cores). llama.cpp defaults to cpu_count() batch
    threads, which the quota throttles into ~30x slower inference. Use the quota
    (capped at 16: measured optimum) unless explicitly requested."""
    if requested and requested > 0:
        return requested
    env = os.environ.get("LLAMA_N_THREADS")
    if env and env.isdigit():
        return int(env)
    cpus = os.cpu_count() or 4
    try:
        q, p = open("/sys/fs/cgroup/cpu.max").read().split()
        if q != "max":
            cpus = max(1, int(int(q) / int(p)))
    except Exception:
        pass
    return max(4, min(16, int(cpus * 0.6)))


def sniff_audio_format(b: bytes) -> str | None:
    """Detect container from magic bytes. Returns one of wav/mp3/flac/mp4/webm/ogg or None."""
    if len(b) < 12:
        return None
    if b[:4] == b"RIFF" and b[8:12] == b"WAVE":
        return "wav"
    if b[:4] == b"fLaC":
        return "flac"
    if b[:3] == b"ID3" or (b[0] == 0xFF and (b[1] & 0xE0) == 0xE0):
        return "mp3"
    if b[4:8] == b"ftyp":
        return "mp4"          # m4a / mp4 / 3gp family → PyAV transcodes
    if b[:4] == b"\x1aE\xdf\xa3":
        return "webm"         # webm / mkv
    if b[:4] == b"OggS":
        return "ogg"
    return None


# ---------------------------------------------------------------------------
# Error classification (updatev2_1 §2)
# ---------------------------------------------------------------------------
class ASRErrorCategory(str, Enum):
    MIC_PERMISSION = "mic_permission"
    UPLOAD_FAILED = "upload_failed"
    FORMAT_UNSUPPORTED = "format_unsupported"
    DECODE_FAILED = "decode_failed"
    MODEL_UNAVAILABLE = "model_unavailable"
    INFERENCE_EXCEPTION = "inference_exception"
    CUDA_OOM = "cuda_oom"
    POST_PROCESSING = "post_processing"
    TIMEOUT = "timeout"


def _classify_error(e: Exception) -> ASRErrorCategory:
    """Classify an exception into an error category for diagnostics."""
    msg = str(e).lower()
    if "cuda" in msg or "out of memory" in msg or "oom" in msg:
        return ASRErrorCategory.CUDA_OOM
    # Format errors — wrong codec/container, magic bytes mismatch
    if ("format must be" in msg or "pyav" in msg or "format=" in msg
            or "unsupported audio format" in msg or "magic bytes" in msg
            or "only supports" in msg or "未安装" in str(e)):
        return ASRErrorCategory.FORMAT_UNSUPPORTED
    # Decode errors — file readable but content corrupted/empty/un-decodable
    if ("无法打开" in str(e) or "无法转码" in str(e) or "解码后为空" in str(e)
            or "failed to load media" in msg or "corrupted data" in msg
            or "missing helper" in msg or "decode failed" in msg):
        return ASRErrorCategory.DECODE_FAILED
    if "mmproj" in msg or "not loaded" in msg:
        return ASRErrorCategory.MODEL_UNAVAILABLE
    if "timeout" in msg or "timed out" in msg:
        return ASRErrorCategory.TIMEOUT
    return ASRErrorCategory.INFERENCE_EXCEPTION


def _err_response(request_id: str, category: str, detail: str) -> JSONResponse:
    return JSONResponse(
        {"detail": detail, "request_id": request_id, "category": category},
        status_code=503,
    )


def _probe_audio(audio_bytes: bytes, filename: str) -> dict:
    """Probe audio metadata: size, format, sample_rate, channels, duration.

    Uses PyAV if available; falls back to WAV header parsing for .wav files.
    Never raises — metadata is best-effort for diagnostics.
    """
    meta: dict = {"size": len(audio_bytes), "filename": filename}
    declared = Path(filename).suffix.lower().lstrip(".") or "wav"
    ext = sniff_audio_format(audio_bytes) or declared
    meta["format"] = ext
    if ext != declared:
        meta["declared_format"] = declared
    # Try WAV header parsing first (fast, no deps)
    if ext == "wav":
        try:
            with _wave.open(_io.BytesIO(audio_bytes), "rb") as w:
                meta["sample_rate"] = w.getframerate()
                meta["channels"] = w.getnchannels()
                meta["sample_width"] = w.getsampwidth()
                frames = w.getnframes()
                if meta["sample_rate"] > 0:
                    meta["duration_s"] = round(frames / meta["sample_rate"], 2)
        except Exception:
            pass
    # Try PyAV for non-WAV or if WAV parsing failed
    if "duration_s" not in meta:
        try:
            import av
            inp = av.open(_io.BytesIO(audio_bytes), mode="r")
            stream = inp.streams.audio[0]
            meta["sample_rate"] = stream.rate
            meta["channels"] = stream.channels
            if stream.duration and stream.time_base:
                meta["duration_s"] = round(float(stream.duration * stream.time_base), 2)
            inp.close()
        except Exception:
            pass  # best-effort
    return meta


def _find_mmproj(model_path: str) -> str | None:
    """Locate mmproj gguf in the model directory."""
    d = Path(model_path).parent
    # exact name
    p = d / "mmproj-Qwen3-ASR-1.7B-bf16.gguf"
    if p.exists():
        return str(p)
    # glob fallback
    for f in d.glob("mmproj*.gguf"):
        return str(f)
    return None


class _ASR:
    def __init__(self, model_path: str, threads: int | None = None):
        self.model_path = model_path
        self.requested_threads = threads
        self.mmproj_path = _find_mmproj(model_path)
        self._load()

    def _load(self):
        """Load (or reload) the gguf model + chat handler."""
        logger.info(f"loading gguf {self.model_path} ...")
        if self.mmproj_path:
            logger.info(f"mmproj found: {self.mmproj_path}")
        else:
            logger.warning("no mmproj file found — audio input will not work")
        t0 = time.time()
        from llama_cpp import Llama
        from llama_cpp.llama_chat_format import Qwen3ASRChatHandler

        chat_handler = None
        if self.mmproj_path:
            chat_handler = Qwen3ASRChatHandler(
                mmproj_path=self.mmproj_path,
                verbose=False,
            )
        self.n_threads = effective_threads(getattr(self, "requested_threads", None))
        logger.info(f"llama.cpp threads={self.n_threads}")
        self.llm = Llama(
            model_path=self.model_path,
            n_gpu_layers=-1,   # all layers on GPU (no-op on CPU-only builds)
            n_ctx=2048,        # reduced context to save VRAM
            n_threads=self.n_threads,
            n_threads_batch=self.n_threads,
            verbose=False,
            chat_handler=chat_handler,
        )
        logger.info(f"loaded in {time.time()-t0:.1f}s")

    def _reload(self):
        """Reload the model to recover from corrupted internal state.

        llama.cpp's experimental audio input can corrupt state on repeated
        calls. If reset() fails, reload the entire model rather than silently
        continuing with broken state (which causes hangs/garbage output).
        """
        logger.warning("reloading model to recover state...")
        try:
            del self.llm
        except Exception as e:
            logger.warning(f"del self.llm failed: {e}")
        import gc; gc.collect()
        self._load()

    def transcribe(self, audio_bytes: bytes, filename: str = "audio.wav") -> str:
        if not self.mmproj_path:
            raise RuntimeError("mmproj not loaded — cannot process audio input")
        # Determine format from the CONTENT (magic bytes), not the client-supplied
        # filename: a phone may upload m4a/mp4 bytes labelled .wav (or vice versa).
        ext = sniff_audio_format(audio_bytes) or (Path(filename).suffix.lower().lstrip(".") or "wav")
        # Browser MediaRecorder produces webm/opus (Chrome) or mp4 (Safari).
        # Transcode webm/mp4 → raw PCM WAV (16kHz mono s16) via PyAV.
        if ext in ("webm", "mp4", "m4a", "ogg", "opus"):
            audio_bytes, ext = _transcode_to_wav(audio_bytes, ext)
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        # Reset context between calls — llama.cpp's experimental audio input
        # corrupts internal state on 2nd call. If reset fails, reload.
        try:
            self.llm.reset()
        except Exception as e:
            logger.error(f"reset failed: {e}\n{traceback.format_exc()}")
            self._reload()
        try:
            # Qwen3ASRChatHandler DROPS all user text and only extracts audio.
            # The transcription instruction MUST go in a system message — the
            # handler's chat template only reads text from role=='system'.
            # Without a system message, the model gets an empty system block
            # and returns empty content (text_len=0).
            out = self.llm.create_chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an advanced multilingual Speech-to-Text model. "
                            "Accurately transcribe the audio into text in its original "
                            "spoken language. Ignore background noise, filler words, "
                            "and stutters. Output only the transcription."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_audio",
                             "input_audio": {"data": audio_b64, "format": ext}},
                        ],
                    },
                ],
                max_tokens=512,
            )
            # Log raw model output for diagnostics — the model may return None,
            # thinking tokens, or unexpected tags that get stripped to empty.
            raw_msg = out["choices"][0]["message"]
            raw_content = raw_msg.get("content")
            raw_reasoning = raw_msg.get("reasoning_content", "")
            logger.info(f"raw_model_output content={raw_content!r} "
                        f"reasoning={raw_reasoning!r} "
                        f"finish_reason={out['choices'][0].get('finish_reason')}")
            raw = raw_content or ""
            # Strip model artifacts: "language Chinese<asr_text>" prefix, tags
            import re
            # Pattern: "language <Lang><asr_text>transcription</asr_text>"
            # The model outputs language detection + asr_text tags.
            # Extract text between <asr_text> and </asr_text> if present.
            m = re.search(r'<asr_text>(.*?)</asr_text>', raw, re.DOTALL)
            if m:
                raw = m.group(1).strip()
            else:
                # No closing tag — model stopped early. Extract after <asr_text>.
                if '<asr_text>' in raw:
                    raw = raw.split('<asr_text>', 1)[-1].strip()
                else:
                    # Strip "language <something>" prefix
                    raw = re.sub(r'^language\s+\S*\s*', '', raw)
                    raw = raw.replace("<asr_text>", "").replace("</asr_text>", "")
            # Some Qwen3-ASR builds put the transcription in reasoning_content
            # when content is empty. Fallback: use reasoning if content is blank.
            if not raw and raw_reasoning:
                raw = raw_reasoning.strip()
                m2 = re.search(r'<asr_text>(.*?)</asr_text>', raw, re.DOTALL)
                if m2:
                    raw = m2.group(1).strip()
                elif '<asr_text>' in raw:
                    raw = raw.split('<asr_text>', 1)[-1].strip()
                else:
                    raw = re.sub(r'^language\s+\S*\s*', '', raw)
                logger.info(f"using reasoning_content as fallback: {raw!r}")
            raw = raw.strip()
            return raw
        except Exception as e:
            raise RuntimeError(f"asr decode failed: {e}")


def _transcode_to_wav(audio_bytes: bytes, src_format: str) -> tuple[bytes, str]:
    """Transcode webm/mp4/etc → 16kHz mono s16 WAV using PyAV.

    Returns (wav_bytes, "wav"). Raises RuntimeError with clear message on failure.
    """
    try:
        import av
        import numpy as np
    except ImportError:
        raise RuntimeError(f"PyAV 未安装，无法转码 {src_format} 音频。请安装 pyav 或使用 WAV 格式。")

    target_sr = 16000
    try:
        inp = av.open(_io.BytesIO(audio_bytes), mode="r", format=src_format)
    except Exception:
        # Try auto-detect container
        try:
            inp = av.open(_io.BytesIO(audio_bytes), mode="r")
        except Exception as e:
            raise RuntimeError(f"无法打开音频 (format={src_format}): {e}")

    resampler = av.AudioResampler(format="s16", layout="mono", rate=target_sr)
    pcm_parts: list[bytes] = []
    for frame in inp.decode(audio=0):
        for rf in resampler.resample(frame):
            arr = rf.to_ndarray()
            pcm_parts.append(arr.tobytes())
    inp.close()
    pcm = b"".join(pcm_parts)
    if not pcm:
        raise RuntimeError("音频解码后为空（可能损坏或过短）")

    # Write PCM into a WAV container
    buf = _io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # s16
        w.setframerate(target_sr)
        w.writeframes(pcm)
    return buf.getvalue(), "wav"


def _make_test_wav(duration_s: float = 1.0, freq: int = 440) -> bytes:
    """Generate a minimal sine-wave WAV for smoke testing."""
    sr = 16000
    n = int(sr * duration_s)
    pcm = b""
    for i in range(n):
        val = int(16000 * math.sin(2 * math.pi * freq * i / sr))
        pcm += struct.pack("<h", val)
    buf = _io.BytesIO()
    with _wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


_A: _ASR | None = None
_lock = asyncio.Lock()  # serialize transcribe calls — llama.cpp audio input crashes on concurrent use
app = FastAPI(title="asr-server")


@app.post("/v1/audio/transcriptions")
async def transcribe(file: bytes = File(...), model: str = Form("Qwen3-ASR-1.7B"),
                     filename: str = Form("audio.wav")):
    """Transcribe audio with full diagnostics: request_id, stage timing,
    audio metadata, error classification."""
    request_id = f"asr-{uuid.uuid4().hex[:12]}"
    t_start = time.time()
    stages: dict = {}

    if not file:
        return _err_response(request_id, ASRErrorCategory.UPLOAD_FAILED.value, "音频内容为空")

    # Probe audio metadata (best-effort, never raises)
    audio_meta = _probe_audio(file, filename)
    logger.info(f"[{request_id}] audio_meta={audio_meta}")

    if _A is None:
        return _err_response(request_id, ASRErrorCategory.MODEL_UNAVAILABLE.value,
                             "ASR 模型未初始化")

    async with _lock:
        loop = asyncio.get_running_loop()

        # Transcode stage (if needed)
        t0 = time.time()
        try:
            # transcribe() handles transcoding internally; we time the whole call
            # and classify errors at the boundary.
            text = await asyncio.wait_for(
                loop.run_in_executor(None, _A.transcribe, file, filename),
                timeout=180,
            )
        except asyncio.TimeoutError:
            stages["total_ms"] = (time.time() - t_start) * 1000
            logger.error(f"[{request_id}] TIMEOUT stages={stages}\n{traceback.format_exc()}")
            return _err_response(request_id, ASRErrorCategory.TIMEOUT.value,
                                 "ASR 推理超时（180s），请缩短音频或重试")
        except RuntimeError as e:
            cat = _classify_error(e)
            stages["total_ms"] = (time.time() - t_start) * 1000
            logger.error(f"[{request_id}] {cat.value}: {e}\n{traceback.format_exc()}")
            return _err_response(request_id, cat.value, str(e))
        except Exception as e:
            cat = _classify_error(e)
            stages["total_ms"] = (time.time() - t_start) * 1000
            logger.error(f"[{request_id}] {cat.value}: {e}\n{traceback.format_exc()}")
            return _err_response(request_id, cat.value, str(e))

        stages["total_ms"] = (time.time() - t_start) * 1000
        logger.info(f"[{request_id}] success text_len={len(text)} latency_ms={stages['total_ms']:.0f}")
        return {
            "text": text, "model": model,
            "request_id": request_id,
            "latency_ms": round(stages["total_ms"]),
            "audio_meta": audio_meta,
        }


@app.get("/v1/audio/test")
async def smoke_test():
    """Minimal audio smoke test: generate 1s sine WAV → transcribe → return result.

    Verifies the full pipeline (transcode + inference) is functional.
    """
    request_id = f"test-{uuid.uuid4().hex[:12]}"
    if _A is None or _A.mmproj_path is None:
        return JSONResponse(
            {"request_id": request_id, "ready": False,
             "error": "model not loaded"},
            status_code=503,
        )
    wav = _make_test_wav(1.0)
    t0 = time.time()
    loop = asyncio.get_running_loop()
    try:
        text = await asyncio.wait_for(
            loop.run_in_executor(None, _A.transcribe, wav, "test.wav"),
            timeout=180,
        )
        latency = (time.time() - t0) * 1000
        return {
            "request_id": request_id,
            "ready": True,
            "text": text,
            "latency_ms": round(latency),
            "note": "synthetic sine wave — empty text is expected (no speech)",
        }
    except asyncio.TimeoutError:
        return JSONResponse(
            {"request_id": request_id, "ready": False,
             "category": "timeout", "error": "smoke test timed out (180s)"},
            status_code=503,
        )
    except Exception as e:
        cat = _classify_error(e)
        logger.error(f"[{request_id}] smoke test failed: {e}\n{traceback.format_exc()}")
        return JSONResponse(
            {"request_id": request_id, "ready": False,
             "category": cat.value, "error": str(e)},
            status_code=503,
        )


@app.get("/health")
async def health():
    return {"status": "ok", "model": "Qwen3-ASR-1.7B"}


@app.get("/ready")
async def ready():
    """Deep readiness check: model loaded + mmproj present."""
    is_ready = _A is not None and _A.mmproj_path is not None
    return {"ready": is_ready, "model_loaded": _A is not None,
            "mmproj_loaded": _A is not None and _A.mmproj_path is not None}


def main():
    global _A
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=7872)
    p.add_argument("--model-path", required=True)
    p.add_argument("--threads", type=int, default=0, help="llama.cpp threads (0 = auto from CPU quota)")
    args = p.parse_args()
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
    _A = _ASR(args.model_path, threads=args.threads or None)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
