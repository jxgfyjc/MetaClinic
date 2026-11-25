"""LLM / OCR / ASR clients with timeouts, bounded retries, error classification and tolerant JSON parsing.

No fake data: failures surface as `ModelError(code, message)`, which the job engine turns into a visible
failed state with a retry. `XY_AI_MODE=fake` (tests only) swaps in canned, clearly-marked responses.
"""
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from server.config import get_settings

PROMPT_DIR = Path(__file__).parent / "prompts"
_LLM_SEM_NAME = "llm"


class ModelError(Exception):
    """A model call failed. `code` is stable and shown to the user; `retryable` drives job retry."""

    def __init__(self, code: str, message: str, *, retryable: bool = True, raw: str = ""):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable
        self.raw = raw[:2000]            # excerpt of what the model actually returned — kept on failed modules so a bad run is diagnosable


@dataclass
class LLMResult:
    text: str
    model: str
    latency_ms: int
    finish_reason: str = ""


def load_prompt(name: str) -> tuple[str, str]:
    """(text, version) — version = file name stem, stamped on every output for traceability."""
    p = PROMPT_DIR / f"{name}.md"
    return p.read_text(encoding="utf-8"), name


# --------------------------------------------------------------------------- tolerant JSON
def _strip_fence(t: str) -> str:
    t = t.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        t = t[nl + 1:] if nl >= 0 else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def extract_json(text: str) -> dict:
    """Parse the model's JSON object. Raises ModelError('bad_output') if nothing parseable exists.

    Handles ```json fences, leading chain-of-thought and trailing chatter by trying the outermost balanced
    `{...}` candidates (largest first). Truncated output (unbalanced) is reported as `truncated`.
    """
    if not text or not text.strip():
        raise ModelError("empty_output", "模型返回为空")
    t = _strip_fence(text)
    try:
        v = json.loads(t)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    starts = [m.start() for m in re.finditer(r"\{", t)]
    end = t.rfind("}")
    for s in starts[:40]:
        if end > s:
            try:
                v = json.loads(t[s:end + 1])
                if isinstance(v, dict) and v:
                    return v
            except Exception:
                continue
    opens, closes = t.count("{"), t.count("}")
    if opens > closes:
        raise ModelError("truncated", "模型输出被截断（JSON 不完整），可重试或缩小请求范围")
    raise ModelError("bad_output", "模型输出无法解析为 JSON")


# --------------------------------------------------------------------------- LLM
async_client_kwargs = {"limits": httpx.Limits(max_connections=8)}


def _classify_http(e: Exception) -> ModelError:
    if isinstance(e, httpx.ConnectError):
        return ModelError("model_unreachable", "模型服务未启动或无法连接")
    if isinstance(e, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.ConnectTimeout)):
        return ModelError("model_timeout", "模型推理超时")
    if isinstance(e, httpx.HTTPStatusError):
        body = ""
        try:
            body = e.response.text[:200]
        except Exception:
            pass
        low = body.lower()
        if "out of memory" in low or "oom" in low or "cuda" in low:
            return ModelError("model_oom", "模型显存不足（GPU 繁忙），请稍后重试")
        return ModelError("model_http_error", f"模型服务返回 {e.response.status_code}：{body}",
                          retryable=e.response.status_code >= 500)
    return ModelError("model_error", f"模型调用失败：{type(e).__name__}: {e}")


def chat(messages: list[dict], *, role: str = "main", max_tokens: int = 1200, timeout: float = 300.0,
         json_mode: bool = True, temperature: float = 0.3) -> LLMResult:
    """Synchronous chat completion (jobs run in worker threads). role: main | tcm."""
    s = get_settings()
    if s.ai_mode == "fake":
        from server.ai import fakes
        return fakes.chat(messages, role=role)
    headers: dict[str, str] = {}
    extra: dict[str, Any] = {}
    if role == "ext":
        # OPTIONAL external model (style-profile case extraction only; callers must have verified de-identification + the admin switch)
        if not (s.style_ext_base_url and s.style_ext_model):
            raise ModelError("ext_not_configured", "未配置外部模型（XY_STYLE_EXT_BASE_URL / XY_STYLE_EXT_MODEL）", retryable=False)
        base, model = s.style_ext_base_url, s.style_ext_model
        if s.style_ext_api_key:
            headers["Authorization"] = f"Bearer {s.style_ext_api_key}"
        if s.style_ext_extra_json.strip():
            try:
                extra = json.loads(s.style_ext_extra_json)
            except ValueError:
                raise ModelError("ext_not_configured", "XY_STYLE_EXT_EXTRA_JSON 不是合法 JSON", retryable=False) from None
        if not base.rstrip("/").endswith(("/v1", "/chat/completions")) and "deepseek" not in base:
            base = base.rstrip("/") + "/v1"
    else:
        base = s.qwen_base_url if role == "main" else s.shizhen_base_url
        model = s.llm_main_model if role == "main" else s.llm_tcm_model
        if s.llm_api_key:
            headers["Authorization"] = f"Bearer {s.llm_api_key}"
        if s.llm_extra_json.strip():
            try:
                extra = json.loads(s.llm_extra_json)
            except ValueError:
                raise ModelError("llm_not_configured", "XY_LLM_EXTRA_JSON 不是合法 JSON", retryable=False) from None
    payload: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, **extra}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    t0 = time.time()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0)) as c:
            r = c.post(base.rstrip("/") + "/chat/completions", json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
    except Exception as e:                       # noqa: BLE001 - classified below
        raise _classify_http(e) from e
    display_model = model
    if role == "main" and s.llm_main_label:
        display_model = s.llm_main_label
    elif role == "tcm" and s.llm_tcm_label:
        display_model = s.llm_tcm_label
    try:
        ch = data["choices"][0]
        return LLMResult(text=ch["message"]["content"] or "", model=display_model, latency_ms=int((time.time() - t0) * 1000),
                         finish_reason=ch.get("finish_reason", ""))
    except (KeyError, IndexError, TypeError) as e:
        raise ModelError("bad_response", f"模型响应格式异常：{e}") from e


def chat_json(system: str, user: str, *, role: str = "main", max_tokens: int = 1200, timeout: float = 300.0,
              image_b64: str | None = None, image_mime: str = "image/jpeg", retries: int = 1) -> tuple[dict, LLMResult]:
    """Chat → parsed JSON object. One bounded retry on truncated/bad output (with a firmer reminder)."""
    content: Any = user
    if image_b64:
        content = [{"type": "text", "text": user},
                   {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_b64}"}}]
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    last: ModelError | None = None
    for attempt in range(retries + 1):
        res = chat(msgs, role=role, max_tokens=max_tokens + (400 * attempt), timeout=timeout)
        try:
            return extract_json(res.text), res
        except ModelError as e:
            e.raw = e.raw or res.text[:2000]
            last = e
            msgs = msgs + [{"role": "assistant", "content": res.text[:1500]},
                           {"role": "user", "content": "上一次输出不是完整合法的 JSON。请只输出一个完整的 JSON 对象，以 { 开头，更精简。"}]
    assert last is not None
    raise last


# --------------------------------------------------------------------------- OCR / ASR
OCR_PROMPT = "请识别并提取图片中的所有文字内容，包括检验项目名称、数值、单位、参考范围和异常标志。按原始格式输出，不要补全看不清的内容。"


def ocr_image(image_bytes: bytes, mime: str = "image/jpeg", *, timeout: float = 240.0) -> tuple[str, str, int]:
    """Returns (text, engine, latency_ms). PaddleOCR-VL only — a general VLM fallback misread values in testing
    (ALT 19.0→13.0), so a degraded engine is NOT silently substituted; the doctor sees a clear failure and retry."""
    s = get_settings()
    if s.ai_mode == "fake":
        from server.ai import fakes
        return fakes.ocr(image_bytes)
    url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
    payload = {"model": s.ocr_model, "max_tokens": 2200, "temperature": 0.1,
               "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}},
                                                         {"type": "text", "text": OCR_PROMPT}]}]}
    t0 = time.time()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0)) as c:
            r = c.post(s.ocr_base_url.rstrip("/") + "/chat/completions", json=payload)
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"] or ""
    except Exception as e:                       # noqa: BLE001
        me = _classify_http(e)
        raise ModelError("ocr_" + me.code.removeprefix("model_"), "OCR：" + me.message) from e
    if not text.strip():
        raise ModelError("ocr_empty", "OCR 未识别到任何文字（图片可能过暗/过糊/不是报告）", retryable=False)
    return text, s.ocr_model, int((time.time() - t0) * 1000)


def asr_transcribe(audio_bytes: bytes, filename: str = "audio.wav", *, timeout: float = 240.0) -> tuple[str, dict, int]:
    """Returns (text, meta, latency_ms). Empty transcript is an explicit failure, never silent success."""
    s = get_settings()
    if s.ai_mode == "fake":
        from server.ai import fakes
        return fakes.asr(audio_bytes)
    t0 = time.time()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0)) as c:
            r = c.post(s.asr_base_url.rstrip("/") + "/audio/transcriptions",
                       files={"file": (filename, audio_bytes)}, data={"model": "Qwen3-ASR-1.7B", "filename": filename})
    except Exception as e:                       # noqa: BLE001
        me = _classify_http(e)
        raise ModelError("asr_" + me.code.removeprefix("model_"), "语音识别：" + me.message) from e
    if r.status_code != 200:
        try:
            d = r.json()
        except Exception:
            d = {"detail": r.text[:200]}
        cat = d.get("category", "upstream_error")
        retry = cat in ("timeout", "model_unavailable", "inference_exception", "cuda_oom")
        raise ModelError("asr_" + cat, f"语音识别失败（{cat}）：{d.get('detail', '')}", retryable=retry)
    d = r.json()
    text = (d.get("text") or "").strip()
    if not text:
        raise ModelError("asr_no_speech", "未识别到语音内容（录音过短、音量过小或无清晰人声）", retryable=False)
    return text, d.get("audio_meta", {}), int((time.time() - t0) * 1000)


def service_status() -> dict:
    """Live readiness of every model endpoint (used by /system/status and the admin dashboard)."""
    s = get_settings()
    if s.demo_local_persona:
        return {k: {"ok": True, "latency_ms": 2} for k in ("llm_main", "llm_tcm", "asr", "ocr")}
    out: dict[str, dict] = {}
    for key, base in (("llm_main", s.qwen_base_url), ("llm_tcm", s.shizhen_base_url), ("asr", s.asr_base_url), ("ocr", s.ocr_base_url)):
        root = base.rstrip("/").removesuffix("/v1")
        t0 = time.time()
        try:
            r = httpx.get(root + "/health", timeout=2.5)
            out[key] = {"ok": r.status_code == 200, "latency_ms": int((time.time() - t0) * 1000)}
        except Exception as e:                   # noqa: BLE001
            out[key] = {"ok": False, "error": type(e).__name__}
    if s.ai_mode == "fake":
        out = {k: {"ok": True, "fake": True} for k in out}
    return out
