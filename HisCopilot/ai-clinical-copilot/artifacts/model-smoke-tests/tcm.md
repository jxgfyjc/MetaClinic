# ShizhenGPT-7B-Omni (tcm) smoke test
- date: 2026-10-07
- env: hiscopilot-tcm (transformers 4.51.0, torch 2.14.1+cu130)
- runtime: transformers AutoModelForCausalLM (bf16, GPU1, trust_remote_code)
- load_time_s: 11.5
- vram_mb: ~17600
- endpoint: http://127.0.0.1:7871/v1/chat/completions
- result: ✅ PASS — 中医证候候选 (风寒袭肺/痰湿阻肺/阴虚肺燥) with supporting_evidence + confidence
- notes: 输出含 ```json fence + 分析说明, orchestrator 用 _safe_json 提取
- status: ✅ PASS
