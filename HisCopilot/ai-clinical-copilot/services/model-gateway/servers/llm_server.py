"""Generic LLM server — transformers + OpenAI-compatible chat completions.

Hosts Qwen3.8-27B (clinical_main) and ShizhenGPT-7B-Omni (tcm) depending on --role.
Each role runs in its own process + conda env (transformers version differs).
instruct #5/#72: business layer sees only the OpenAI-compatible URL.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class _LLM:
    def __init__(self, model_path: str, role: str):
        print(f"[{role}] loading {model_path} ...", flush=True)
        t0 = time.time()
        import torch
        from transformers import AutoModelForCausalLM, AutoProcessor

        self.role = role
        self.torch = torch
        # ShizhenGPT-7B-Omni needs trust_remote_code + custom modeling_shizhen.py
        trust = role == "tcm"
        # processor (tokenizer + image preprocessor)
        self.processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=trust)
        # ShizhenGPT registers only AutoModelForCausalLM → Qwen2_5_VL_Audio_ForConditionalGeneration.
        # AutoModelForImageTextToText / AutoModel both fail to resolve the custom config class.
        # For the tcm role, use AutoModelForCausalLM explicitly (trust_remote_code picks up the
        # custom modeling file). For VLM roles, prefer AutoModelForImageTextToText.
        try:
            if role == "tcm":
                self.model = AutoModelForCausalLM.from_pretrained(
                    model_path, torch_dtype=torch.bfloat16, trust_remote_code=trust,
                ).to("cuda")
            else:
                from transformers import AutoModelForImageTextToText
                self.model = AutoModelForImageTextToText.from_pretrained(
                    model_path, torch_dtype=torch.bfloat16, trust_remote_code=trust,
                ).to("cuda")
        except Exception as e:
            print(f"[{role}] primary load failed ({e}); trying AutoModel", flush=True)
            from transformers import AutoModel
            self.model = AutoModel.from_pretrained(
                model_path, torch_dtype=torch.bfloat16, trust_remote_code=trust,
            ).to("cuda")
        self.model.eval()
        print(f"[{role}] loaded in {time.time()-t0:.1f}s", flush=True)

    def chat(self, messages: list[dict], json_mode: bool = False, max_tokens: int = 1500) -> str:
        torch = self.torch
        # Normalize content parts: convert image_url (data URL / path) to PIL images
        # and rebuild message content as {"type":"image","image":PIL} + text parts,
        # which the processor's chat template handles correctly (inserts image tokens).
        norm_messages: list[dict] = []
        images: list = []
        for m in messages:
            c = m.get("content")
            if isinstance(c, list):
                parts = []
                for p in c:
                    if p.get("type") == "text":
                        parts.append({"type": "text", "text": p["text"]})
                    elif p.get("type") == "image_url":
                        url = p["image_url"]["url"]
                        if url.startswith("data:"):
                            import base64, io
                            from PIL import Image as _PILImage
                            img = _PILImage.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGB")
                        else:
                            from PIL import Image as _PILImage
                            img = _PILImage.open(url).convert("RGB")
                        parts.append({"type": "image", "image": img})
                        images.append(img)
                # order: images first then text (matches processor expectation)
                parts_sorted = [p for p in parts if p.get("type") == "image"] + \
                               [p for p in parts if p.get("type") == "text"]
                norm_messages.append({"role": m["role"], "content": parts_sorted})
            else:
                norm_messages.append({"role": m["role"], "content": c})

        # Build prompt via processor's apply_chat_template (inserts image placeholders).
        # Qwen3-family models emit a <think> reasoning block by default; for structured
        # clinical output we disable thinking so the model emits JSON directly (no CoT
        # eating the token budget, no truncated JSON). The template accepts
        # enable_thinking=False; if this kwarg is unsupported we fall back gracefully.
        try:
            prompt = self.processor.apply_chat_template(
                norm_messages, tokenize=False, add_generation_prompt=True,
                enable_thinking=False,
            )
        except (TypeError, Exception):
            try:
                prompt = self.processor.apply_chat_template(
                    norm_messages, tokenize=False, add_generation_prompt=True,
                )
            except Exception:
                prompt = "\n".join(f"{m['role']}: {m['content']}" for m in norm_messages) + "\nassistant:"

        proc_kwargs = {"text": prompt, "return_tensors": "pt"}
        if images:
            proc_kwargs["images"] = images
        inputs = self.processor(**proc_kwargs).to(self.model.device)
        # Pass through generate-compatible keys (drop mm_token_type_ids etc.)
        gen_kwargs = {k: v for k, v in inputs.items()
                      if k in ("input_ids", "attention_mask", "pixel_values",
                               "image_grid_thw", "pixel_values_videos")}
        with torch.inference_mode():
            out = self.model.generate(
                **gen_kwargs,
                max_new_tokens=max_tokens,
                do_sample=True,
                temperature=0.3,
                top_p=0.9,
            )
        gen_ids = out[0][gen_kwargs["input_ids"].shape[1]:]
        try:
            text = self.processor.batch_decode([gen_ids], skip_special_tokens=True)[0]
        except Exception:
            text = self.processor.tokenizer.decode(gen_ids, skip_special_tokens=True)
        return text


_L: _LLM | None = None
app = FastAPI(title="llm-server")


class ChatReq(BaseModel):
    model: str = ""
    messages: list[dict]
    max_tokens: int = 1500
    temperature: float = 0.3
    top_p: float = 0.9
    response_format: dict | None = None


@app.post("/v1/chat/completions")
async def chat(req: ChatReq):
    json_mode = bool(req.response_format and req.response_format.get("type") == "json_object")
    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(
            None, _L.chat, req.messages, json_mode, req.max_tokens
        )
    except Exception as e:
        raise HTTPException(503, f"llm failed: {e}")
    return {
        "id": f"{_L.role}-{uuid.uuid4().hex[:16]}",
        "object": "chat.completion",
        "model": req.model or _L.role,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.get("/health")
async def health():
    return {"status": "ok", "role": _L.role if _L else "unknown"}


@app.get("/ready")
async def ready():
    return {"ready": _L is not None}


def main():
    global _L
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--model-path", required=True)
    p.add_argument("--role", required=True)
    args = p.parse_args()
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")
    _L = _LLM(args.model_path, args.role)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
