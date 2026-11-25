# Qwen3.8-27B (clinical_main) smoke test
- date: 2026-10-07
- env: comfui (transformers 5.18, torch 2.14.1+cu130)
- runtime: transformers AutoModelForImageTextToText (bf16, GPU1)
- load_time_s: 53.7 (first) / 17.8 (subsequent)
- vram_mb: ~50800 (权重 ~52GB bf16)
- endpoint: http://127.0.0.1:7870/v1/chat/completions
- text_reasoning: ✅ PASS — structured medical history JSON from free-text 病情
- vlm_ocr: ✅ PASS — extracted lab report text (HbA1c 7.8% HIGH, eGFR 48 LOW, etc.)
- patches applied:
  1. AutoModelForImageTextToText (not AutoModelForCausalLM, which drops vision)
  2. filter mm_token_type_ids from generate kwargs
  3. message content normalized to {type:image, image:PIL} so processor inserts image placeholder tokens
- notes: 输出包含 reasoning preamble (xhigh effort); orchestrator strips via JSON extraction
- status: ✅ PASS (text + vision)
