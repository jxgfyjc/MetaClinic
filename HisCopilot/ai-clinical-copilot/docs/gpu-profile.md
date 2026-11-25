# GPU Profile — 显存分配与调度

> 2026-10-07 Phase 0 实测。两块 A100-SXM4-80GB。

## 实测基线

```
$ nvidia-smi --query-gpu=index,memory.total,memory.used,memory.free --format=csv
0, 81920 MiB, 46063 MiB, 35280 MiB
1, 81920 MiB,   423 MiB, 80920 MiB
```

GPU0 已被 **ComfyUI `main.py` (PID 178680)** 占用 ~46GB —— 用户明确要求不得关闭。
→ **本系统所有模型放 GPU1**，`CUDA_VISIBLE_DEVICES=1`。

## 显存预算（bf16，权重 only，不含 KV cache）

| 模型 | 参数 | bf16 显存 | 备注 |
|---|---:|---:|---|
| Qwen3.8-27B (clinical_main) | 27B | ~52 GB | 18 shards / 55.5GB 权重文件 |
| ShizhenGPT-7B-Omni (tcm) | 7B | ~17 GB | 4 shards / 17.8GB |
| ShizhenGPT-32B-VL (备选) | 32B | ~63 GB | 非首选，备用 |
| HuatuoGPT-o1-72B (second_opinion) | 72B | ~144 GB | 单卡装不下；需 TP=2 或 q4 |
| Qwen3-Embedding-8B | 8B | ~15 GB | 按需 |
| Qwen3-Reranker-8B | 8B | ~16 GB | 按需 |
| Qwen3-ASR-1.7B (gguf q8) | 1.7B | ~2 GB | gguf 轻量 |
| PaddleOCR-VL-1.6 (gguf q8) | — | ~1.5 GB | gguf 轻量 |

## 调度方案（GPU1, 80GB）

### 常驻组合（核心推理路径）
```
clinical_main (Qwen3.8-27B, ~52G) + tcm (ShizhenGPT-7B-Omni, ~17G) = ~69G
```
剩余 ~11G 不足以同时常驻 embedding/reranker。

### 按需 load（余量分时复用）
- `embedding` / `reranker` / `asr` / `ocr`：由 model-gateway 在请求时 load，空闲超时 unload。
- 或：把 `embedding` 常驻（~15G，与 clinical_main 52+15=67G 仍 < 80G），`reranker` 按需。
- KV cache：clinical_main 留 ~10G KV，tcm 留 ~3G。

### 预期并发
- 单用户门诊场景，串行请求为主，并发 1–2。
- clinical_main MTP + linear_attention，长上下文 262144 token；MVP 用 8K–32K 上下文以控显存。

## GPU0（ComfyUI 占用，~46G）

- 已用 46G，剩余 ~35G。
- 本系统 **不常驻** GPU0。
- `second_opinion`（72B bf16 ~144G）需 TP=2 且 GPU0 余量不足 → 默认关闭；未来若加载 q4 量化（~40G）+ GPU1 也可行，但需 ComfyUI 让出部分显存，当前不做。

## 性能目标（instruct #82）

| 模块 | 目标 | 手段 |
|---|---|---|
| 文本临床分析 | <15s | transformers bf16，结果缓存 |
| 中医分析 | <15s | transformers bf16 |
| OCR | <10s | llama.cpp gguf |
| ASR | 接近实时 | gguf |
| RAG | <2s | embedding 常驻 + pgvector/sqlite-vec |
| 首屏 | <3s | Next.js dev / 静态预渲染 |

## 待记录（model-gateway 跑通后回填）

```
| 模型 | 加载耗时 | P50 延迟 | P95 延迟 | 显存占用(含KV) | 并发 |
```
