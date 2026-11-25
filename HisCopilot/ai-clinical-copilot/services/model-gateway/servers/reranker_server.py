"""Reranker server — Qwen3-Reranker-8B via FlagEmbedding.

Cohere-style `/v1/rerank` on GPU1.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class _Rer:
    def __init__(self, model_path: str):
        print(f"[rer] loading {model_path} ...", flush=True)
        t0 = time.time()
        from FlagEmbedding import FlagReranker
        self.m = FlagReranker(model_path, use_fp16=True)
        # ensure pad token set (Qwen3 reranker may lack one for batched scoring)
        try:
            tok = getattr(self.m, "tokenizer", None)
            if tok is not None and getattr(tok, "pad_token", None) is None:
                tok.pad_token = tok.eos_token or tok.unk_token
        except Exception as e:
            print(f"[rer] pad-token fix skipped: {e}", flush=True)
        print(f"[rer] loaded in {time.time()-t0:.1f}s", flush=True)

    def rerank(self, query: str, docs: list[str], top_n: int = 8) -> list[dict]:
        pairs = [[query, d] for d in docs]
        # score one-by-one to avoid batch padding issues
        scores: list[float] = []
        for pair in pairs:
            s = self.m.compute_score(pair, normalize=True)
            scores.append(float(s) if isinstance(s, (int, float)) else float(s[0]))
        ranked = sorted(
            [{"index": i, "document": {"text": docs[i]}, "relevance_score": float(s)}
             for i, s in enumerate(scores)],
            key=lambda x: x["relevance_score"], reverse=True,
        )[:top_n]
        return ranked


_R: _Rer | None = None
app = FastAPI(title="reranker-server")


class RerankReq(BaseModel):
    model: str = "Qwen3-Reranker-8B"
    query: str
    documents: list[str]
    top_n: int = 8


@app.post("/v1/rerank")
async def rerank(req: RerankReq):
    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(None, _R.rerank, req.query, req.documents, req.top_n)
    except Exception as e:
        raise HTTPException(503, f"rerank failed: {e}")
    return {"model": req.model, "results": res}


@app.get("/health")
async def health():
    return {"status": "ok", "model": "Qwen3-Reranker-8B"}


@app.get("/ready")
async def ready():
    return {"ready": _R is not None}


def main():
    global _R
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=7874)
    p.add_argument("--model-path", required=True)
    args = p.parse_args()
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
    _R = _Rer(args.model_path)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
