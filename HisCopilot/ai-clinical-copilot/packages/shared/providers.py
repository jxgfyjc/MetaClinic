"""Provider abstractions.

instruct #5/#72: business layer never knows vLLM/SGLang/Transformers/llama.cpp.
All model access goes through these OpenAI-compatible providers + a gateway.
instruct #30/#61/#80: timeout / retry / circuit-breaker / fallback-state,
but NEVER fake AI data (instruct #50).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_model, get_settings


class AIUnavailable(Exception):
    """Raised when a model service is down. Surfaced as PARTIAL/FAILED, not fake data."""


@dataclass
class LLMResponse:
    text: str
    latency_ms: int
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    run_id: str = ""


def _client(base_url: str, timeout: float = 120.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, timeout=timeout)


async def _call_with_retry(fn, *, attempts: int = 2, tag: str = "ai"):
    """Bounded retry — instruct #61: do not retry infinitely."""
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(min=1, max=4),
            retry=retry_if_exception_type((httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError)),
            reraise=True,
        ):
            with attempt:
                return await fn()
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError, OSError) as e:
        raise AIUnavailable(f"{tag} unavailable: {e}") from e


# ---------------------------------------------------------------------------
# Providers — each maps to a role in models.yaml
# ---------------------------------------------------------------------------
class LLMProvider:
    """OpenAI-compatible chat completions for text/vlm LLMs."""

    def __init__(self, role: str):
        m = get_model(role)
        if m is None or not m.enabled:
            raise AIUnavailable(f"model role '{role}' not configured/enabled")
        self.role = role
        self.base_url = m.base_url
        self.model_id = m.model_id

    async def chat(self, messages: list[dict], *, json_mode: bool = False,
                   timeout: float = 120.0, max_tokens: int | None = None) -> LLMResponse:
        rid = uuid.uuid4().hex[:16]
        t0 = time.time()
        settings = get_settings()

        if settings.enable_ai_failure_simulation and os.environ.get(f"FAIL_{self.role.upper()}") == "1":
            raise AIUnavailable(f"{self.role} failure-simulation ON")

        async def _do():
            async with _client(self.base_url, timeout) as c:
                payload: dict[str, Any] = {"model": self.model_id, "messages": messages}
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}
                if max_tokens is not None:
                    payload["max_tokens"] = max_tokens
                r = await c.post("/chat/completions", json=payload)
                r.raise_for_status()
                return r.json()

        try:
            data = await _call_with_retry(_do, tag=self.role)
        except AIUnavailable:
            raise
        except Exception as e:
            raise AIUnavailable(f"{self.role} call failed: {e}") from e

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        return LLMResponse(
            text=text,
            latency_ms=int((time.time() - t0) * 1000),
            model=self.model_id,
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            run_id=rid,
        )

    async def chat_vision(self, messages: list[dict], image_url: str, *,
                          json_mode: bool = True, timeout: float = 150.0,
                          max_tokens: int | None = None) -> LLMResponse:
        """VLM call with an inline image_url in the user message."""
        # OpenAI-compatible multimodal: append image content part to last user msg
        msgs = [dict(m) for m in messages]
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i]["role"] == "user":
                content = msgs[i].get("content")
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                content.append({"type": "image_url", "image_url": {"url": image_url}})
                msgs[i]["content"] = content
                break
        return await self.chat(msgs, json_mode=json_mode, timeout=timeout, max_tokens=max_tokens)


class EmbeddingProvider:
    def __init__(self, role: str = "embedding"):
        m = get_model(role)
        if m is None or not m.enabled:
            raise AIUnavailable(f"embedding model not configured")
        self.base_url = m.base_url
        self.model_id = m.model_id

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async def _do():
            async with _client(self.base_url, timeout=60.0) as c:
                r = await c.post("/embeddings", json={"model": self.model_id, "input": texts})
                r.raise_for_status()
                return r.json()
        try:
            data = await _call_with_retry(_do, tag="embedding")
        except AIUnavailable:
            raise
        except Exception as e:
            raise AIUnavailable(f"embedding failed: {e}") from e
        return [d["embedding"] for d in sorted(data.get("data", []), key=lambda x: x["index"])]


class RerankerProvider:
    """OpenAI-compatible rerank (Cohere-style /v1/rerank)."""

    def __init__(self, role: str = "reranker"):
        m = get_model(role)
        if m is None or not m.enabled:
            raise AIUnavailable("reranker model not configured")
        self.base_url = m.base_url
        self.model_id = m.model_id

    async def rerank(self, query: str, documents: list[str], top_n: int = 8) -> list[dict]:
        async def _do():
            async with _client(self.base_url, timeout=60.0) as c:
                r = await c.post(
                    "/rerank",
                    json={"model": self.model_id, "query": query, "documents": documents, "top_n": top_n},
                )
                r.raise_for_status()
                return r.json()
        try:
            data = await _call_with_retry(_do, tag="reranker")
        except AIUnavailable:
            raise
        except Exception as e:
            raise AIUnavailable(f"rerank failed: {e}") from e
        return data.get("results", [])


class ASRProvider:
    def __init__(self, role: str = "asr"):
        m = get_model(role)
        if m is None or not m.enabled:
            raise AIUnavailable("asr model not configured")
        self.base_url = m.base_url
        self.model_id = m.model_id

    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.wav") -> str:
        async def _do():
            async with _client(self.base_url, timeout=180.0) as c:
                files = {"file": (filename, audio_bytes)}
                data = {"model": self.model_id}
                r = await c.post("/audio/transcriptions", files=files, data=data)
                r.raise_for_status()
                return r.json()
        try:
            data = await _call_with_retry(_do, tag="asr")
        except AIUnavailable:
            raise
        except Exception as e:
            raise AIUnavailable(f"asr failed: {e}") from e
        return data.get("text", "")


class OCRProvider:
    """PaddleOCR-VL via OpenAI-compatible VLM endpoint (image→structured text)."""

    def __init__(self, role: str = "ocr"):
        m = get_model(role)
        if m is None or not m.enabled:
            raise AIUnavailable("ocr model not configured")
        self.base_url = m.base_url
        self.model_id = m.model_id

    async def extract(self, image_bytes: bytes, mime: str = "image/png", prompt: str = "") -> str:
        import base64
        b64 = base64.b64encode(image_bytes).decode()
        data_url = f"data:{mime};base64,{b64}"
        msgs = [
            {"role": "system", "content": "You are an OCR engine for medical reports. Output structured markdown preserving tables and numeric values. Do not invent content."},
            {"role": "user", "content": [
                {"type": "text", "text": prompt or "Extract all text, tables, and numeric lab values from this report image. Output structured markdown."},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ]
        async def _do():
            async with _client(self.base_url, timeout=120.0) as c:
                r = await c.post("/chat/completions", json={
                    "model": self.model_id, "messages": msgs, "max_tokens": 2000
                })
                r.raise_for_status()
                return r.json()
        try:
            data = await _call_with_retry(_do, tag="ocr")
        except AIUnavailable:
            raise
        except Exception as e:
            raise AIUnavailable(f"ocr failed: {e}") from e
        return data.get("choices", [{}])[0].get("message", {}).get("content", "")


import os  # noqa: E402  (placed late to keep providers section clean)
