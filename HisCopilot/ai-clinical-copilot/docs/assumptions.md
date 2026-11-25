# Assumptions

> 当不确定时，按 best engineering judgment 决策并记录于此。instruct #89 要求不暂停询问，继续推进。

---

## A1. Docker 不可用 → host process 模式

**背景**：探测发现 `docker: command not found`。
**决策**：所有服务（API、model-gateway、各模型 server、前端 dev server）以 **host process + conda env** 运行。`docker-compose.yml` 与 `infra/docker/` 仍保留 Dockerfile/compose 作为未来部署参考，但 MVP 启动脚本不依赖 docker。
**影响**：`make dev` / `start-dev.sh` 用 `conda run -n hiscopilot` + 后台进程 + `nohup`，而非 `docker compose up`。

## A2. 隔离 conda env

**背景**：用户明确要求"若要用到环境一定 anaconda 创建隔离虚拟环境"。`comfui` env 是 ComfyUI 主进程的运行环境（占用 GPU0 46GB），**不得污染**。
**决策**：新建 `hiscopilot` env（Python 3.12）作为主后端运行环境。`tcm` 模型因需 `transformers==4.51.0`（与 clinical_main 的 5.18 冲突），单独建 `hiscopilot-tcm` 子环境。模型 server 各自用其兼容 env 启动，业务层通过 HTTP 调用，互不干扰。

## A3. 端口避让

**背景**：用户提供 8000（aivideo）与 8188（ComfyUI）已开放且不可关闭。
**决策**：本系统端口分配——
- API (FastAPI): **8001**
- Model-Gateway: **7860**
- clinical_main server: 7870（内部，gateway 反代）
- tcm server: 7871
- asr server: 7872
- embedding server: 7873
- reranker server: 7874
- ocr server (llama-server): 7875
- second_opinion server: 7876
- 前端 (Next.js): **3000**
- Mock HIS: 复用 API 8001 的 `/mock-his/*` 路由
- Postgres: 5432（conda 装或 docker 兜底未启用前用 SQLite）
- Redis: 6379（未启用前用进程内缓存）

## A4. PostgreSQL/Redis/MinIO 未安装 → MVP 用轻量替代

**背景**：环境无 docker，conda 默认源未含 postgres server。
**决策**：
- DB：MVP 用 **SQLite + sqlite-vec**（pgvector 的轻量替代，接口与 SQLAlchemy 兼容）；`HISAdapter`/`Knowledge` 抽象层保持 `Session` 注入，未来切换 Postgres+pgvector 仅改 connection string。
- Cache：进程内 `cachetools`/`dict` 抽象 `CacheProvider`，未来切 Redis。
- ObjectStore：本地文件系统抽象 `LocalObjectStore`，未来切 MinIO。
所有抽象在 `packages/shared/` 提供，业务层只依赖接口。

## A5. GPU0 被占用 → 全部模型放 GPU1

**背景**：ComfyUI 在 GPU0 占 46GB，本系统不得挤占（用户要求不关闭）。
**决策**：所有模型 `CUDA_VISIBLE_DEVICES=1`。clinical_main(52G)+tcm(17G) 常驻 GPU1 共 ~69G，embedding/reranker/ocr/asr 按需 load。second_opinion（72B）需双卡或量化，默认关闭。

## A6. ASR 权重不完整 → 用 gguf

**背景**：`Qwen3-ASR-1.7B` 仅 `model-00001-of-00002.safetensors` + 缺 config.json，但 `Qwen3-ASR-1.7B-Q8_0.gguf` 完整。
**决策**：ASR 走 `llama.cpp`（`llama-server` 加载 gguf，OpenAI-compatible `/v1/audio/transcriptions`）。待后续补全 safetensors 再评估 transformers 路径。instruct #98 不主动重新下载。

## A7. HuatuoGPT-o1-72B 仍在下载 → second_opinion 默认关闭

**背景**：探测时 `hf download HuatuoGPT-o1-72B` 进程仍在运行（24/31 shards）。
**决策**：`models.yaml` 中 `second_opinion.enabled=false`。下载完成后由用户/后续脚本显式启用。72B bf16 需 ~144GB，单卡装不下，需 TP=2 或 q4 量化——MVP 不实现，留接口。

## A8. 无外部训练语料 → 知识库最小种子

**背景**：未发现 Shizhen corpus / TCMChat / Huatuo 数据集。
**决策**：不下载（instruct #75/#98）。知识库先用内置 demo 种子（指南摘要/经方/药品/协定方各若干条），验证 RAG pipeline；后续通过 `POST /api/knowledge/documents` 上传真实文档。`docs/resource-manifest.md` 记录缺失资源。

## A9. transformers 版本冲突 → 多 env 多进程

**背景**：clinical_main 需 transformers 5.18（comfui env），tcm 需 4.51.0。
**决策**：每个模型 server 独立进程 + 独立 env，对外暴露 OpenAI-compatible HTTP。业务层（ai-orchestrator）只用 HTTP，不感知 runtime/transformers 版本。

## A10. 未安装 vLLM/SGLang/llama.cpp → 装到 hiscopilot env

**背景**：comfui env 无 vllm/sglang/llama_cpp/paddle。
**决策**：按需在 `hiscopilot` env `pip install llama-cpp-python`（OCR/ASR gguf）；transformers 路径用 `hiscopilot` env 自带的（按模型要求 pin）。vLLM 评估留到 second_opinion 阶段。

## A11. 真实 HIS 未提供 → MockHISAdapter

**背景**：instruct #7 明确 MVP 不接真实 HIS。
**决策**：实现 `MockHISAdapter`，接口与未来 `HospitalHISAdapter` 解耦。所有 HIS 写操作带 doctor_id/encounter_id/timestamp/request_id/signature_id。

## A12. 真实模型调用 → 不伪装

**背景**：instruct #50/#88 禁止假 AI。
**决策**：所有 AI 端点真实调用 model-gateway → 真实模型 server。模型不可用时返回明确 `AI unavailable` 状态，不 fallback 成假数据。仅做结果缓存（同 input_hash + model + prompt_version 复用，instruct #84）。

## A13. PaddleOCR-VL-1.6 gguf 加载失败 → OCR 降级

**背景**：`paddleocr-vl-1.6-q8_0.gguf` 在 llama-cpp 0.3.36 报 `key not found in model: qwen2vl.context_length`，无法加载。`model.safetensors` 路径也因目录缺 tokenizer/processor 文件（只有权重+gguf+.cache）无法用 `AutoProcessor.from_pretrained` 加载。
**决策**：OCR 角色降级——由 `clinical_main`（Qwen3.8-27B，自带 vision_config）作为 VLM 兜底执行检查报告图像→结构化文本，明确标注"OCR via Qwen3.8 VLM (PaddleOCR-VL gguf 损坏)"。不伪装。后续需补全 PaddleOCR-VL 完整 repo 或换 PaddleOCR 经典版。
**影响**：`models.yaml` 中 `ocr.runtime` 标注为 `transformers_fallback`，状态 `degraded`。

## A14. Qwen3-ASR-1.7B gguf 未验证 → ASR 标记 partial

**背景**：ASR gguf 可能存在与 OCR gguf 类似的 metadata 缺失；safetensors 也不完整（缺 shard-2 + config）。
**决策**：ASR 服务端点保留但 MVP 标记 `partial`；若 gguf 加载失败则返回 503（不伪装）。患者语音流程在 ASR 不可用时降级为"文字描述病情"路径（instruct #11 已有文字描述页面）。


## A15. sqlite-vec 不能在 SQLAlchemy aiosqlite connect 钩子内加载

**背景**：`packages/shared/db.py` 原尝试在 `@event.listens_for(sync_engine, "connect")` 内调用 `sqlite_vec.load(dbapi_conn)`，但 aiosqlite 的 DBAPI 包装层在自身后台线程里跑，从该同步钩子访问会触发 "SQLite objects created in a thread can only be used in that same thread" 或包装层无 `load_extension`。
**决策**：不在 SQLAlchemy 引擎上加载 sqlite-vec。vec0 虚拟表（`knowledge_chunk_vec`，dim=4096，匹配 Qwen3-Embedding-8B 原生维度）由 `init_engine()` 末尾的 `_init_vec0_table()` 用独立 aiosqlite 连接创建；`KnowledgeStore.add_chunks/search` 也各自开独立 aiosqlite 连接、`enable_load_extension(True)` → `load_extension(loadable_path())`。SQLAlchemy 引擎只管关系表（patients/encounters/ai_runs/audit...）。RAG 向量检索失败时降级为关键词检索（instruct #30）。

## A16. vec0 rowid 必须是 INTEGER；字符串主键不能直接作为向量表 rowid

**背景**：`knowledge_chunks` 主键是 24 位字符串 id，但 sqlite-vec 的 `vec0` 虚拟表 `rowid` 列只接受 INTEGER。
**决策**：在 `knowledge_chunks` 增加 `vec_rowid INTEGER` 列，写入时取 `MAX(rowid)+1`，向量行用该整数 rowid；检索时 `JOIN ... ON c.vec_rowid = v.rowid`。

## A17. clinical_main 与 embedding/reranker 共占 GPU1 → KV cache OOM

**背景**：Qwen3.8-27B bf16 ≈ 53GB，Qwen3-Embedding-8B ≈ 15GB，Qwen3-Reranker-8B ≈ 16GB。三者同时常驻 GPU1(80GB) 后只剩 ~12GB，长上下文（系统提示+病例 JSON）KV cache 触发 OOM（`CUDA out of memory ... allocate 20.00 MiB`，逻辑 device 0 = 物理 GPU1）。
**决策**：MVP 常驻 `clinical_main` + `tcm`（共 ~70GB）；`embedding`/`reranker` 按需启停——做 RAG 入库/检索时启 embedding（必要时停 clinical_main 腾显存），做 rerank 时启 reranker。API 启动时若 embedding 不可用，知识库 seeding 跳过、检索降级为关键词（已在 seed_demo.py 与 RAGPipeline 中捕获异常，不阻塞 API，instruct #30）。生产环境需 TP/Multi-GPU 或换更小 embedding（如 bge-m3）。

## A18. ShizhenGPT-7B-Omni 只注册 AutoModelForCausalLM，AutoModel/AutoModelForImageTextToText 失败

**背景**：ShizhenGPT config.json `auto_map` 仅含 `AutoModelForCausalLM → modeling_shizhen.Qwen2_5_VL_Audio_ForConditionalGeneration`，未注册 `AutoModel`/`AutoModelForImageTextToText`，导致 `AutoModel.from_pretrained` 报 "Unrecognized configuration class Qwen2_5_VLConfig for this kind of AutoModel"。
**决策**：`llm_server.py` 对 `role == "tcm"` 显式用 `AutoModelForCausalLM.from_pretrained(..., trust_remote_code=True)`；其余 role 仍优先 `AutoModelForImageTextToText`。TCM 文本辨证已验证可用（输出 `气阴两虚证` 等 JSON）。

## A19. Qwen3.8-27B 默认输出 ild-CoT 思考链，截断会破坏 JSON

**背景**：开启 `response_format=json_object` 后，Qwen3.8-27B 仍先输出长段中文思考过程（非 ild 标签，而是正文推理），最后才给出 JSON。当 `max_new_tokens=1500` 时 JSON 往往被截断（最末 `}` 缺失，`_safe_json` 提取失败 → 模块 `OK` 但内容空）。
**决策**：`llm_server.py` 在 `apply_chat_template` 时传 `enable_thinking=False`（Qwen3 模板支持该 kwarg），让模型直接出 JSON；同时 `_safe_json` 改为"取最后一个 `{...}` 块"并兼容 markdown fence（```json ... ```）。验证后 western/synthesis/prescription 均能稳定解析出结构化字段。

## A20. orchestrator 串行 await 导致 clinical_main OOM 后整链 503（已修复）

**背景**：`run()` 原 `await self._western(); await self._tcm(); await self._tongue()` 串行，且任一 runner 异常会冒泡到 `app.py` 直接 503，违背 instruct #30/#61。
**决策**：改为 `asyncio.create_task` + `gather`（instruct #44 真正并行），每个 runner 内部已 `try/except AIUnavailable → ModuleStatus.UNAVAILABLE`；`run()` 永不抛 AI 异常，只返回 PARTIAL/SUCCEEDED/FAILED。验证：clinical_main OOM 时 western=unavailable、tcm=ok，整体 PARTIAL，API 200。

## A21. 前端采用单 Next.js 应用承载 doctor/patient/admin 三端（MVP）

**背景**：instruct #70 期望 doctor-web / patient-web / admin-web 三端，但 MVP 阶段拆三个独立 Next.js 应用会增加构建/部署成本且无功能差异。
**决策**：单 `apps/doctor-web`（Next.js 15 App Router + TS + Tailwind）承载 `/`(工作台) `/patients` `/encounters/[eid]`(AI 分析+处方签字+提交 HIS) `/knowledge`(RAG 检索) `/patient`(随访端) `/admin`(药品/协定方字典) 六类页面。后续可按端拆分。
