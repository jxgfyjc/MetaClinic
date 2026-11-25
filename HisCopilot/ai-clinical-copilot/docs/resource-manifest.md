# Resource Manifest — 资源清单

> 2026-10-07 探测。受 instruct #75/#98 约束：**不主动下载新资源**。

## 模型权重（本地存在）

| 资源名 | 路径 | 大小 | License | 是否完整 | 用途 |
|---|---|---:|---|---|---|
| Qwen3.8-27B | `models/Qwen3.8-27B` | 52G | Qwen license | ✅ 18/18 shards | 临床主力推理（西医/综合/多模态） |
| ShizhenGPT-7B-Omni | `models/ShizhenGPT-7B-Omni` | 17G | apache-2.0 | ✅ 4/4 shards | 中医四诊多模态 |
| ShizhenGPT-32B-VL | `models/ShizhenGPT-32B-VL` | 63G | apache-2.0 | ✅ 14/14 shards | 中医图文（备选，更大） |
| HuatuoGPT-o1-72B | `models/HuatuoGPT-o1-72B` | 86G(目标) | apache-2.0 | ⏳ 24/31 shards（hf download 进行中） | 疑难第二意见（默认关闭） |
| Qwen3-ASR-1.7B | `models/Qwen3-ASR-1.7B` | 6.0G | Qwen license | ⚠ safetensors 不全；gguf 完整 | 语音→文本 |
| Qwen3-Embedding-8B | `models/Qwen3-Embedding-8B` | 15G | Qwen license | ✅ 4/4 shards | 向量检索 |
| Qwen3-Reranker-8B | `models/Qwen3-Reranker-8B` | 16G | Qwen license | ✅ 5/5 shards | 重排序 |
| PaddleOCR-VL-1.6 | `models/PaddleOCR-VL-1.6` | 3.2G | PaddleOCR license | ✅ gguf 完整 + safetensors | 检查报告 OCR |

模型根：`/home/ma-user/modelarts/user-job-dir/yujc/HisCopilot/models`

## 训练语料 / 知识库数据集（本地未发现）

| 资源名 | 路径 | 是否存在 | License | 用途 | 处置 |
|---|---|---|---|---|---|
| TCM-Pretrain-Data-ShizhenGPT | — | ❌ | FreedomIntelligence | TCM 预训练 | 不下载，知识库用上传文档替代 |
| TCM-Instruction-Tuning-ShizhenGPT | — | ❌ | FreedomIntelligence | TCM 指令 | 同上 |
| TCMChat | — | ❌ | — | 中医对话语料 | 同上 |
| Huatuo 诊疗数据 | — | ❌ | — | 医案 | 同上 |

→ MVP 知识库用内置种子（`data/demo/seed_knowledge.jsonl`）+ `POST /api/knowledge/documents` 上传。

## 运行时 / 库

| 组件 | 是否安装 | 位置 / 处置 |
|---|---|---|
| conda | ✅ 26.5.3 | `yujc/anaconda3` |
| env: comfui | ✅ | ComfyUI 专用，**不污染**（含 torch 2.14.1+cu130, transformers 5.18） |
| env: hiscopilot | ✅ 新建 | 本系统主后端运行环境 |
| env: hiscopilot-tcm | 待建 | ShizhenGPT-7B-Omni 专用（transformers==4.51.0） |
| vLLM | ❌ | 未装；MVP 用 transformers/llama.cpp |
| SGLang | ❌ | 未装 |
| llama-cpp-python | ❌ | 待装到 hiscopilot（OCR/ASR gguf） |
| paddlepaddle | ❌ | 未装；OCR 走 gguf 替代 |
| PostgreSQL | ❌ | MVP 用 SQLite+sqlite-vec |
| Redis | ❌ | MVP 用进程内缓存 |
| MinIO | ❌ | MVP 用本地文件系统 |
| Node.js | ✅（npm-global） | 前端用 |
| Docker | ❌ | 不可用，全 host process |
