# PaddleOCR-VL-1.6 (ocr) smoke test — DEGRADED
- date: 2026-10-07
- gguf path: FAILED — llama-cpp 0.3.36: "key not found in model: qwen2vl.context_length"
- safetensors path: FAILED — directory lacks tokenizer/processor files
- fallback: clinical_main (Qwen3.8-27B VLM) acts as OCR — ✅ PASS (see clinical_main.md)
- status: ⚠ DEGRADED (real OCR via Qwen3.8 VLM, PaddleOCR-VL gguf 损坏, assumptions A13)
