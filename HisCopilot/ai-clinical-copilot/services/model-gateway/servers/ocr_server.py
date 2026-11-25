"""OCR server — PaddleOCR-VL-1.6 gguf + mmproj via llama-cpp-python.

V1.0: Rewritten to use PaddleOCRChatHandler + llama-cpp (gguf+mmproj).
The old transformers path failed due to missing processor/tokenizer files.
OpenAI-compatible /v1/chat/completions on GPU1.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


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


def _find_mmproj(model_path: str) -> str | None:
    d = Path(model_path).parent
    p = d / "PaddleOCR-VL-1.6-mmproj.gguf"
    if p.exists():
        return str(p)
    for f in d.glob("*mmproj*.gguf"):
        return str(f)
    return None


class _OCR:
    def __init__(self, model_path: str, threads: int | None = None):
        self.model_path = model_path
        self.mmproj_path = _find_mmproj(model_path)
        self.n_threads = effective_threads(threads)
        print(f"[ocr] llama.cpp threads={self.n_threads}", flush=True)
        print(f"[ocr] loading gguf {model_path} ...", flush=True)
        if self.mmproj_path:
            print(f"[ocr] mmproj found: {self.mmproj_path}", flush=True)
        else:
            print("[ocr] WARNING: no mmproj — VLM image input won't work", flush=True)
        t0 = time.time()
        from llama_cpp import Llama
        from llama_cpp.llama_chat_format import PaddleOCRChatHandler

        chat_handler = None
        if self.mmproj_path:
            chat_handler = PaddleOCRChatHandler(
                mmproj_path=self.mmproj_path, verbose=False)
        self.llm = Llama(
            model_path=model_path,
            n_gpu_layers=-1,
            n_ctx=2048,
            n_threads=self.n_threads,
            n_threads_batch=self.n_threads,
            verbose=False,
            chat_handler=chat_handler,
        )
        print(f"[ocr] loaded in {time.time()-t0:.1f}s", flush=True)

    def chat(self, messages: list[dict], max_tokens: int = 2000) -> str:
        try:
            out = self.llm.create_chat_completion(
                messages=messages, max_tokens=max_tokens, temperature=0.1)
            return out["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"ocr chat failed: {e}")


_O: _OCR | None = None
app = FastAPI(title="ocr-server")


class ChatReq(BaseModel):
    model: str = "PaddleOCR-VL-1.6"
    messages: list[dict]
    max_tokens: int = 2000
    temperature: float = 0.1


@app.post("/v1/chat/completions")
async def chat(req: ChatReq):
    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(None, _O.chat, req.messages, req.max_tokens)
    except Exception as e:
        raise HTTPException(503, f"ocr failed: {e}")
    import uuid as _uuid
    return {
        "id": f"ocr-{_uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "model": req.model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.get("/health")
async def health():
    return {"status": "ok", "model": "PaddleOCR-VL-1.6"}


@app.get("/ready")
async def ready():
    return {"ready": _O is not None and _O.mmproj_path is not None}


def main():
    global _O
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=7875)
    p.add_argument("--model-path", required=True)
    p.add_argument("--threads", type=int, default=0, help="llama.cpp threads (0 = auto from CPU quota)")
    args = p.parse_args()
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
    _O = _OCR(args.model_path, threads=args.threads or None)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
