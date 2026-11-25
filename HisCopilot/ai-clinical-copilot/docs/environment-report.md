# Resource Report — AI Clinical Copilot

> 生成时间：2026-10-07
> 探测者：Phase 0 环境检查
> 状态：✅ 完成

---

## Hardware

| 项 | 值 |
|---|---|
| 平台 | x86_64 |
| OS | Ubuntu 22.04.2 LTS（容器镜像，内核 4.18 EulerOS 宿主） |
| 主机名 | modelarts-job-2aae2fb0-...-worker-0 |
| 用户 | ma-user |
| CPU 核 | 128 |
| 内存 | 1.0 TiB（可用 ~940 GiB） |
| Swap | 0 |
| 磁盘 | overlay 12T，已用 1.0T，可用 10T；8× NVMe 1.5T（未挂载到工作目录） |

## GPU

| GPU | 型号 | 显存 | 探测时已用 | 探测时空闲 | 备注 |
|---|---|---|---|---|---|
| 0 | NVIDIA A100-SXM4-80GB | 81920 MiB | 46063 MiB | 35 GiB | ComfyUI `main.py` (PID 178680) 占用 ~46GB — **不可关闭** |
| 1 | NVIDIA A100-SXM4-80GB | 81920 MiB | 423 MiB | 81 GiB | 空闲，本系统主用 |

驱动：580.126.20 / CUDA Runtime：13.0（nvidia-smi）/ nvcc：11.8（CUDA toolkit）。
`torch 2.14.1+cu130`（comfui env 实测）可识别 2× A100，`cuda.is_available()=True`。

## CUDA

- 驱动支持 CUDA 13.0；nvcc 工具链为 11.8（编译用）。
- 实际 PyTorch 运行时 cu130，GPU 算力 sm_80（A100）兼容。

## Python

- 系统/默认 conda env：`comfui`（Python 3.12.15，torch 2.14.1+cu130, transformers 5.18.0）。
- 已新建隔离 env：`hiscopilot`（Python 3.12.x，conda create）。
- conda：26.5.3，路径 `/home/ma-user/modelarts/user-job-dir/yujc/anaconda3`。

## Docker

- ❌ 不可用（`docker: command not found`）。
- **影响**：所有服务以 **host process + conda env** 方式运行，不使用 docker compose。
- 后续若需要，可在 `infra/docker/` 留 Dockerfile 供未来部署，但 MVP 运行不依赖它。

## Models

| Model | Path | Size | Arch | Runtime | GPU | Status |
|---|---|---:|---|---|---|---|
| Qwen3.8-27B | `models/Qwen3.8-27B` | 52G | qwen3_5 VLM+LLM (64L, h5120, vocab 248320) | transformers (bf16) | 1 | ✅ complete (18/18 shards, index 55.5GB) |
| ShizhenGPT-7B-Omni | `models/ShizhenGPT-7B-Omni` | 17G | qwen2_5_vl_audio (28L, h3584, 含 audio+vision) | transformers 4.51.0 (trust_remote_code) | 1 | ✅ complete (4/4 shards, 17.8GB) |
| ShizhenGPT-32B-VL | `models/ShizhenGPT-32B-VL` | 63G | qwen2_5_vl (64L, h5120) | transformers (bf16) | 1/0 | ✅ complete (14/14 shards, 66.9GB) — 备用，非首选 |
| HuatuoGPT-o1-72B | `models/HuatuoGPT-o1-72B` | 86G | qwen2 (80L, h8192) | transformers / vLLM (TP=2) | 0+1 | ⏳ downloading (24/31 shards，hf download 仍在跑) |
| Qwen3-ASR-1.7B | `models/Qwen3-ASR-1.7B` | 6.0G | qwen3_asr | transformers 或 llama.cpp(gguf) | 1 | ⚠ partial（仅 shard-1 safetensors + 完整 gguf） |
| Qwen3-Embedding-8B | `models/Qwen3-Embedding-8B` | 15G | qwen3 (sentence-transformers) | transformers / FlagEmbedding | 1 | ✅ complete (4/4 shards, 15.1GB) |
| Qwen3-Reranker-8B | `models/Qwen3-Reranker-8B` | 16G | qwen3 (1_LogitScore reranker) | transformers / FlagEmbedding | 1 | ✅ complete (5/5 shards, 16.4GB) |
| PaddleOCR-VL-1.6 | `models/PaddleOCR-VL-1.6` | 3.2G | paddleocr_vl | llama.cpp (q8_0 gguf) | 1 | ✅ complete (gguf 1.48GB 完整) |

模型根目录：`/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/models`（共 ~305GB）。

## Datasets / 语料

- ❌ 未发现 TCM Shizhen corpus / TCMChat / Huatuo 训练数据集目录。
- HF cache 中仅有模型权重，无独立数据集快照。
- **处置**：建立 `docs/resource-manifest.md`，**不主动下载**（受 instruct #75/#98 约束）。
- MVP 知识库先用内置最小种子（指南/方剂/药品 demo 条目），后续接入院内文档。

## OCR

- PaddleOCR-VL-1.6：gguf 完整可用（q8_0），走 `llama.cpp`（需安装 `llama-cpp-python`）。
- 备选：`model.safetensors` 走 transformers（需对应架构支持）。

## Embedding

- Qwen3-Embedding-8B：safetensors 完整，sentence-transformers 风格（含 `1_Pooling`）。
- 走 `sentence-transformers` 或 `FlagEmbedding` 加载，OpenAI-compatible `/v1/embeddings`。

## Reranker

- Qwen3-Reranker-8B：safetensors 完整，FlagEmbedding reranker（`1_LogitScore`）。
- 走 `FlagEmbedding` 或自写 logits scorer。

## Storage

- MinIO：未安装，MVP 用本地目录 `data/` 兜底对象存储（抽象 `ObjectStore` 接口）。
- PostgreSQL：未安装，需 `conda install` 或用 SQLite 兜底启动（见 assumptions）。
- Redis：未安装，MVP 用进程内缓存抽象。

## 已占用端口（**不可关闭**，用户提供）

| 端口 | 进程 | 用途 |
|---|---|---|
| 8000 | aivideo backend (`uvicorn app.main:app`) | AI Video 平台 — **不得关闭** |
| 8188 | ComfyUI (`main.py`) | ComfyUI 服务 — **不得关闭** |

→ 本系统 API 改用 **8001**，Model-Gateway 用 **7860–7866**，前端用 **3000**。

## Problems

1. **Docker 不可用** → 全部 host process 运行；compose 文件仅作未来部署参考。
2. **GPU0 被 ComfyUI 占用 46GB** → 本系统全部模型放 GPU1（80GB）。
3. **GPU1 显存压力**：clinical_main(52G)+tcm(17G)=69G，再加 embedding/reranker/asr/ocr 需分时复用或按需 load。
4. **transformers 版本冲突**：clinical_main 需 5.18（comfui env），tcm 需 4.51.0。→ tcm 用独立 conda 子环境运行（`hiscopilot-tcm`）。
5. **Qwen3-ASR-1.7B 权重不完整**（缺 shard-2 + config）→ MVP 用其 gguf 走 llama.cpp。
6. **HuatuoGPT-o1-72B 仍在下载**（24/31）→ second_opinion 默认关闭，待完成后按需启用。
7. **PostgreSQL/Redis/MinIO 未安装** → MVP 用 SQLite+本地文件+内存抽象，接口预留未来切换。
8. **无外部数据集** → 知识库先种子，不下载。

## Recommended Allocation（详见 docs/gpu-profile.md）

| GPU | 常驻 | 按需/分时 |
|---|---|---|
| 0 | （ComfyUI 已占 46GB，本系统不常驻） | second_opinion 临时（需等下载 + 量化，TP=2 评估） |
| 1 | clinical_main (Qwen3.8-27B, bf16, ~52G) + tcm (ShizhenGPT-7B-Omni, bf16, ~17G) | asr / embedding / reranker / ocr（按需 load，单进程复用 ~12G 余量；必要时 unload 重型模型） |

**Runtime 决策**：
- clinical_main / tcm / asr / embedding / reranker：`transformers`（自定义/标准架构）
- ocr / asr-gguf：`llama.cpp`（`llama-server`，OpenAI-compatible）
- 所有 runtime 统一包装在 `services/model-gateway` 后，业务层只见 OpenAI-compatible API。
