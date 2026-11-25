"""Embedding server — Qwen3-Embedding-8B via sentence-transformers.

OpenAI-compatible `/v1/embeddings` on GPU1. instruct #5/#72: business layer only
sees this URL, never the runtime.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# pre-load model once at import (so GPU mem is reserved before serving)
class _Emb:
    def __init__(self, model_path: str):
        print(f"[emb] loading {model_path} ...", flush=True)
        t0 = time.time()
        from sentence_transformers import SentenceTransformer
        self.model = SentenceTransformer(
            model_path, device="cuda", model_kwargs={"torch_dtype": "bfloat16"}
        )
        self.dim = self.model.get_sentence_embedding_dimension()
        print(f"[emb] loaded in {time.time()-t0:.1f}s, dim={self.dim}", flush=True)

    def encode(self, texts: list[str]) -> list[list[float]]:
        import torch
        with torch.inference_mode():
            v = self.model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return v.tolist()


_E: _Emb | None = None


app = FastAPI(title="embedding-server")


class EmbReq(BaseModel):
    model: str = "Qwen3-Embedding-8B"
    input: list[str] | str


@app.post("/v1/embeddings")
async def embed(req: EmbReq):
    texts = req.input if isinstance(req.input, list) else [req.input]
    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(None, _E.encode, texts)
    except Exception as e:
        raise HTTPException(503, f"embedding failed: {e}")
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(data)],
        "model": req.model,
        "usage": {"prompt_tokens": sum(len(t)//4 for t in texts), "total_tokens": sum(len(t)//4 for t in texts)},
    }


@app.get("/health")
async def health():
    return {"status": "ok", "model": "Qwen3-Embedding-8B", "dim": _E.dim}


@app.get("/ready")
async def ready():
    return {"ready": _E is not None}


def main():
    global _E
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=7873)
    p.add_argument("--model-path", required=True)
    args = p.parse_args()
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
    _E = _Emb(args.model_path)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
