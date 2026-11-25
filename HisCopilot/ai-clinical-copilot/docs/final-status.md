# 最终状态 — AI Clinical Copilot MVP Sandbox

> 私有化 HIS 外挂 AI 中西医门诊临床副驾 · MVP Sandbox 完成度报告（instruct #95）
> 生成日期：2026-10-07

## 一、总体结论

**MVP Sandbox 端到端跑通**：患者→就诊→AI 并行分析（西医/中医/舌诊/知识）→综合→处方草案→医生签字→提交 MockHIS→随访→风险告警 全链路真实模型调用，**无 fake AI**（instruct #50/#88）。AI 失效时降级、不阻塞 HIS（instruct #30）。

| 维度 | 状态 | 说明 |
|---|---|---|
| 环境与模型发现 | ✅ complete | 2×A100-80GB；GPU0 留 ComfyUI（46GB，未动）；GPU1 跑临床模型 |
| 真实模型推理 | ✅ complete | Qwen3.8-27B（西医/综合/VLM）、ShizhenGPT-7B-Omni（中医）真实调用并结构化解析 |
| RAG 知识检索 | ✅ complete（向量+关键词+rerank） | sqlite-vec + Qwen3-Embedding-8B + Qwen3-Reranker-8B；向量不可用时降级关键词 |
| OCR | ⚠ degraded | PaddleOCR-VL gguf 损坏 → clinical_main VLM 兜底（A13） |
| ASR | ⚠ partial | gguf 未完整验证 → 患者端文字描述降级路径（A14） |
| 第二意见 | ⏸ complete-not-loaded | HuatuoGPT-o1-72B 已下载，默认关闭 |
| 后端 API | ✅ running (port 8001) | FastAPI，全部 instruct #71 端点 |
| 前端 | ✅ running (port 3000) | Next.js 15，6 类页面 |
| 端到端病例 | ✅ enc-003 糖尿病 | 5 类典型病例已 seed |
| AI 失效降级 | ✅ verified | instruct #80 失效模拟 + GPU OOM 实测均返回 PARTIAL，非 500 |

## 二、运行中的服务

| 服务 | 端口 | GPU | 状态 |
|---|---|---|---|
| ComfyUI（既有，未动） | 8188 | GPU0 | running（用户既有进程） |
| aivideo backend（既有，未动） | 8000 | — | running（用户既有进程） |
| clinical_main (Qwen3.8-27B) | 7870 | GPU1 | ✅ health ok |
| tcm (ShizhenGPT-7B-Omni) | 7871 | GPU1 | ✅ health ok |
| embedding (Qwen3-Embedding-8B) | 7873 | GPU1 | 按需启停（A17） |
| reranker (Qwen3-Reranker-8B) | 7874 | GPU1 | 按需启停（A17） |
| **API (FastAPI)** | **8001** | — | ✅ health ok |
| **前端 (Next.js)** | **3000** | — | ✅ HTTP 200 |

> 严格遵守用户约束：**未关闭 8000/8188 进程及端口**；所有 Python 环境均用 anaconda 隔离虚拟环境（`hiscopilot` / `hiscopilot-tcm` / `comfui`）。

## 三、instruct 条款对照

| instruct | 要求 | 落地 |
|---|---|---|
| #5/#72 | 业务层只见 OpenAI-compatible URL | `packages/shared/providers.py` 通过 `base_url` 调用，不知 vLLM/transformers |
| #7/#29 | MockHISAdapter，写入带 doctor_id/encounter_id/signature_id | `his_adapter.py` MockHISAdapter |
| #11 | 患者端 | `/patient` 页面 + `/api/followups/*/response` |
| #17/#44 | 西医/中医/舌诊/知识并行 asyncio.gather | `orchestrator.py` `run()` create_task + gather |
| #21 | RAG 只供证据，模型自主推理 | prompts 明示；evidence 仅作为 excerpt 注入 |
| #22/#23 | 处方为候选草案 | prompts `prescription_draft.md` + 前端"候选"标识 |
| #30 | AI 失效降级不阻塞 HIS | runner 内部捕获 AIUnavailable → UNAVAILABLE；API 503 仅在整链硬失败 |
| #31-#34 | 随访+风险 | `followup/rules.py` + `/api/followups/*/response` |
| #43 | 临床工作流 | `ClinicalAnalysisWorkflow` |
| #50/#88 | 真实模型，无 fake | 全部走真实 transformers 推理 |
| #51/#52 | demo 数据 5 类病例 | `seed_demo.py`：respiratory/digestive/metabolic-chronic/tcm-typical/high-risk-followup |
| #56 | /health /ready /metrics | `app.py` |
| #58 | README | `README.md` |
| #61 | 模块失败→PARTIAL | `run()` 面板计数 |
| #65 | HIS 写入仅经 Adapter | `submit-his` 调 `get_his_adapter().create_prescription` |
| #66/#67 | 服务端控状态+签字留痕 | `/api/prescriptions/drafts/*/sign` 审计 |
| #70 | 前端关键页面 | 见上表六类页面 |
| #71 | API 面 | 见 `app.py` |
| #73/#74 | 知识分块+元数据 | `rag.py chunk_text` 500-1200 字 / `metadata_for` |
| #80 | AI 失效模拟 | `FAIL_{ROLE}` + `enable_ai_failure_simulation`（已验证） |
| #84 | 结果缓存 input_hash | `ai_cache_key` + MemoryCache |
| #89 | 自主决策记录 | `docs/assumptions.md` A1–A21 |
| #95 | 本报告 | 本文件 |

## 四、验证记录（E2E）

**enc-003（2 型糖尿病复诊）真实运行**：
- HTTP 200，latency ≈ 260s（4 次 LLM 调用：western + tcm + synthesis + prescription）
- panel：`western:ok tcm:ok tongue:unavailable knowledge:ok synthesis:ok prescription:ok`
- 状态：PARTIAL（舌诊无图片→UNAVAILABLE，符合预期）
- western：3 个鉴别诊断、4 条问题、红旗、建议检查
- tcm：3 个证型候选（气阴两虚/血瘀络阻...）、治法、方药
- synthesis：综合评估 + 模型冲突分析（`model_conflicts.western_vs_tcm`）
- prescription：3 西药候选 + 2 中药方 + 5 条安全提示（含磺胺过敏规避）

**随访风险告警（enc-005 高血压急症随访）**：
- 输入"头晕加重，胸闷，夜间不能平卧，视物模糊"
- risk_level: **high_risk**，escalation: True
- 触发：keyword:胸闷 / 夜间不能平卧 / 视物模糊 + LLM 信号
- 建议立即急诊（instruct #34）

**AI 失效模拟（instruct #80）**：
- `FAIL_CLINICAL_MAIN=1` → LLMProvider 正确抛 AIUnavailable；orchestrator 返回 PARTIAL，非 500

## 五、已知限制 / 后续

1. **OCR/ASR degraded**：PaddleOCR-VL gguf 损坏（A13）、ASR gguf 未验证（A14）；后续补完整 repo。
2. **GPU 显存**：clinical_main + tcm 常驻 ~70GB，embedding/reranker 按需启停（A17）。生产建议多卡或换小 embedding。
3. **第二意见 HuatuoGPT-o1-72B**：已下载未加载（72B bf16 需 TP=2 或量化）。
4. **SQLite MVP**：单机；生产切 PostgreSQL+pgvector、Redis、MinIO（config 已留开关）。
5. **前端单应用**：doctor/patient/admin 合一（A21），后续可按端拆分。
6. **HIS**：MockHISAdapter；`enable_real_his` 切真实 HospitalHISAdapter（实现 Protocol 即可）。

## 六、关键文件

- 后端：`services/api/app.py`、`services/ai-orchestrator/orchestrator.py`、`services/knowledge/rag.py`、`services/followup/rules.py`
- 共享：`packages/shared/{config,providers,db,cache,his_adapter,audit}.py`
- 模型网关：`services/model-gateway/servers/{llm,embedding,reranker,ocr,asr}_server.py`
- 模型注册：`infra/models.yaml`（base_url 已对齐 7870-7876）
- 提示词：`prompts/*.md`（v1.0）
- 前端：`apps/doctor-web/app/`（6 类页面）
- 种子：`scripts/seed_demo.py`
- 文档：`docs/{environment-report,resource-manifest,gpu-profile,assumptions,final-status}.md`
- 决策记录：`docs/assumptions.md` A1–A21
