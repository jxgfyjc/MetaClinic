# 象元 AI 诊疗 — 中西医结合门诊临床副驾（私有化部署）

> 真实模型推理 · 真实 RAG · 真实 OCR/ASR · 患者→医生→AI→审核→MockHIS→随访 端到端
> 所有 AI 输出仅供医生参考，最终由执业医师签字后经 HIS Adapter 提交。

## 一、这是什么

在 **2×A100-80GB** 服务器上私有化部署的中西医结合门诊 AI 临床副驾。**西医 + 中医** 双轨并行分析，RAG 提供证据，AI 草拟处方与理疗方案，医生签字后写入 HIS。当前使用 SQLite + sqlite-vec + 本地文件 + 进程内缓存（生产可切 Postgres+pgvector / Redis / MinIO）。

**严格遵守**：无 fake AI（instruct #50/#88）、无新模型下载（#75/#98）、无微调（#54）、AI 失效降级不阻塞 HIS（#30）、所有 HIS 写入仅经 Adapter（#65）。

## 二、架构

```
┌─────────────┐   ┌──────────────────────────────────────────────┐
│ Next.js     │──▶│ FastAPI (8001)                                │
│ doctor-web  │   │  /api/encounters/{eid}/ai/analyze              │
│ (3000)      │   │  /api/knowledge/search  /api/followups         │
└─────────────┘   │  /api/prescriptions/drafts → sign → submit-his │
                  │  /mock-his/*  /api/patients  /api/alerts       │
                  └──────┬───────────────────────────────────────┘
                         │ asyncio.gather（instruct #44）
                  ┌──────▼──────┐  ┌─────────────────────────┐
                  │ Orchestrator│─▶│ RAG (KnowledgeStore)    │
                  │ 西医/中医/  │  │ Embedding→Vec→Keyword→  │
                  │ 舌诊/知识 → │  │ Fusion→Rerank→Evidence  │
                  │ 综合→处方   │  └─────────────────────────┘
                  └──────┬──────┘
            OpenAI-compatible base_url（业务层不知 vLLM/transformers）
                         │
       ┌─────────┬───────┴───────┬───────────┬──────────┐
       ▼         ▼               ▼           ▼          ▼
  clinical_main  tcm          embedding   reranker   ocr(降级→VLM)
  Qwen3.8-27B    ShizhenGPT   Qwen3-Emb   Qwen3-Rer  (7870 兜底)
  (7870)         -7B-Omni     -8B (7873)  -8B (7874)
                 (7871)
       └─ 各自独立 conda env + 独立进程，GPU1-only（GPU0 留 ComfyUI）
```

## 三、快速启动

> 服务器已有 ComfyUI(8188) 与 aivideo(8000) 在跑——**本系统不碰这两个端口**。

```bash
# 1) 模型服务（clinical_main 在 comfui env；tcm 在 hiscopilot-tcm env）
bash scripts/start-model-servers.sh

# 2) 后端 API（hiscopilot env）
conda activate hiscopilot
cd ai-clinical-copilot
PYTHONPATH=. python -m uvicorn services.api.app:app --host 0.0.0.0 --port 8001
# 启动时自动 seed demo 数据（5 患者 / 5 病例 / 18 药品 / 3 协定方 / 6 知识文档）

# 3) 前端
cd apps/doctor-web && npm install && npm run dev   # http://localhost:3000
```

`.env`（已生成自 `.env.example`）：`APP_PORT=8001`、`CUDA_VISIBLE_DEVICES=1`、各模型 `base_url` 对齐 7870-7876。

## 四、5 类典型病例（instruct #52）

| 就诊 | 类型 | 主诉 |
|---|---|---|
| enc-001 | respiratory（西医为主） | 发热3天伴咽痛咳嗽 |
| enc-002 | digestive（中西医结合） | 反复上腹胀痛2月 |
| enc-003 | metabolic-chronic（慢病） | 2型糖尿病复诊，血糖控制欠佳 **（已跑通 E2E）** |
| enc-004 | tcm-typical（中医辨证） | 失眠3月 |
| enc-005 | high-risk-followup（高危随访） | 高血压急症后随访 |

访问 `http://localhost:3000/encounters/enc-003` → 点"启动 AI 分析"（约 2-5 分钟）→ 查看 western/tcm/synthesis/prescription → "医生签字" → "提交 HIS"。

## 五、模型矩阵

| 角色 | 模型 | 端口 | 状态 |
|---|---|---|---|
| clinical_main | Qwen3.8-27B (VLM+LLM) | 7870 | ✅ |
| tcm | ShizhenGPT-7B-Omni | 7871 | ✅ |
| embedding | Qwen3-Embedding-8B (dim 4096) | 7873 | ✅（按需启停） |
| reranker | Qwen3-Reranker-8B | 7874 | ✅（按需启停） |
| ocr | PaddleOCR-VL-1.6 | 7870 | ⚠ degraded（clinical_main VLM 兜底） |
| asr | Qwen3-ASR-1.7B | 7872 | ⚠ partial |
| second_opinion | HuatuoGPT-o1-72B | 7876 | ⏸ 已下载未加载 |

详见 `infra/models.yaml`、`docs/gpu-profile.md`、`docs/assumptions.md`（A1–A21）。

## 六、关键约束已落实

- **anaconda 隔离环境**：`hiscopilot` / `hiscopilot-tcm` / `comfui`（用户要求）
- **端口 8000/8188 未动**（用户要求）
- **GPU1-only**：`CUDA_VISIBLE_DEVICES=1`，GPU0 留 ComfyUI
- **AI 失效降级**：模块失败 → PARTIAL，不阻塞 HIS（instruct #30/#61/#80 已验证）
- **结果缓存**：`ai_cache_key(encounter+model+prompt_version+knowledge_version+input_hash)`（instruct #84）
- **审计留痕**：`audit.ndjson` + DB mirror，签字/提交 HIS/反馈均记录（instruct #29/#67）
- **HIS 写入仅经 Adapter**：`get_his_adapter()` 工厂（instruct #65）

## 七、文档

- `docs/environment-report.md` — 环境探测
- `docs/resource-manifest.md` — 资源清单
- `docs/gpu-profile.md` — GPU 分配
- `docs/assumptions.md` — 自主决策记录（A1–A21，instruct #89）
- `docs/final-status.md` — MVP 完成度报告（instruct #95）
- `artifacts/model-smoke-tests/` — 各模型冒烟测试结果

## 八、生产化路径

1. SQLite → PostgreSQL + pgvector；MemoryCache → Redis；LocalObjectStore → MinIO（config 已留开关）
2. MockHISAdapter → HospitalHISAdapter（实现 `HISAdapter` Protocol）
3. 补 PaddleOCR-VL 完整 repo / 换 PaddleOCR 经典版；补 ASR safetensors
4. HuatuoGPT-o1-72B 启用（需 TP=2 或量化）
5. 前端按 doctor/patient/admin 拆分；接入 RBAC 登录（roles 已定义）

---

**象元 AI 诊疗 · 患者数据严禁出网 · 所有 AI 输出仅供医生参考**
