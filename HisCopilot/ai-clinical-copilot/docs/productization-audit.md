# 产品化审计 — ai-clinical-copilot

> Phase 2 第一步（update.md 第一原则）。审计日期：2026-10-09。
> 分类：(A) 可保留 / (B) 需重构 / (C) Demo placeholder / (D) 真正可复用业务逻辑

## 1. 前端页面 `apps/doctor-web/app/`

| 路由 | 现状 | 分类 |
|---|---|---|
| `layout.tsx` | 导航壳 + DEMO ONLY 横幅 | (A) |
| `page.tsx` 首页 | 3 个统计卡片 + 硬编码 5 病例列表；**bug**：第 17 行把 encounters 也 fetch 到 `/mock-his/patients`，encounters 数硬编码 "5" | (B) |
| `patients/page.tsx` | 患者列表表格（只读） | (A) |
| `encounters/page.tsx` | **硬编码** `["enc-001".."enc-005"]` 逐个 fetch，无真实列表端点 | (C) |
| `encounters/[eid]/page.tsx` | **seed 数据硬编码在前端 .tsx 的 `ENC_SEED` 常量里**，与 seed_demo.py 重复，API 只取 patient | (C) |
| `encounters/[eid]/AnalyzePanel.tsx` | 核心 UI：启动分析→面板状态→evidence→western→tcm→synthesis→prescription→sign→submit-his。但：①无 Accept/Edit/Reject；②`doctor_id:"dr-001"` 硬编码；③无 streaming，按钮文案"约 2–5 分钟" | (B) |
| `knowledge/page.tsx` | RAG 检索结果（title/tier/source/score/excerpt），但**不显示 chapter/version**；无上传/启停 UI | (A)/(B) |
| `patient/page.tsx` | **硬编码** `fid="followup-005"`/`patient_id:"p-005"`，单场景 | (C) |
| `admin/page.tsx` | 只读药品+协定方列表，无 CRUD，无知识上传/启停/版本管理（后端有 `/api/knowledge/documents` 却无人调用） | (C) |

组件层：**没有 `components/` 目录**，所有 UI 内联在 page.tsx（如 `PrescriptionActions` 定义在 AnalyzePanel 内）。`lib/api.ts` 干净但无 SSE helper。

## 2. API `services/api/app.py`（380 行，全 blocking）

- Mock-HIS 路由（6 条，pass-through）：(A)
- `POST /api/encounters/{eid}/ai/analyze`：**全量阻塞返回**（2-5 分钟）→ (B) 改 SSE/async-job
- `GET /api/ai-runs/{rid}`：只查缓存，不查 DB `ai_runs` 表 → (C)
- `POST /api/ai-outputs/{oid}/feedback`：**只写审计日志**，不持久化 edited_content，不更新 AI 输出状态 → (C) stub
- `GET /api/knowledge/search`：(A)
- `POST /api/knowledge/documents`：可用但无 UI 调用 → (A)
- `POST /api/prescriptions/drafts`：**不写 `prescription_drafts` 表**，原样返回 → (C)
- `PATCH /api/prescriptions/drafts/{pid}`：声称状态→DOCTOR_EDITING 但硬编码返回，无 DB、无服务端状态机 → (C)
- `POST /api/prescriptions/drafts/{pid}/sign`：无密码确认，不写 DB → (C)
- `POST /api/prescriptions/{pid}/submit-his`：**真正经 HISAdapter 写入** → (D) 唯一真正端到端
- `POST /api/followups`：返回假 followup_id，不存 → (C)
- `GET /api/followups`：硬编码 `{"followups":[]}` → (C)
- `POST /api/followups/{fid}/response`：LLM 提取 + rule engine `assess()` → (D)
- `GET /api/alerts`：硬编码 `[]` → (C)

## 3. 数据模型 `packages/shared/db.py`（22 张表）

**关键问题：22 张表全部为空。** seed_demo 只写 MockHISAdapter 内存字典 + `knowledge_chunks`（裸 aiosqlite）。所有 ORM 表（users/patients/encounters/medical_records/lab_reports/tongue_images/ai_runs/ai_outputs/ai_evidence/ai_feedback/knowledge_sources/knowledge_documents/drugs/formula/prescription_drafts/prescription_reviews/followup_plans/followup_responses/followup_risk_events/audit_logs）从未被写入。

- Schema 设计良好 → (D)
- 实际状态：除 `knowledge_chunks` 外全空 → 整体 (C)
- → (B) 必须接 repository 层让真实持久化存在

## 4. AI Workflow `services/ai-orchestrator/orchestrator.py`

- Streaming：**否**，全阻塞。每个模块 `await llm.chat()` 完整返回 → (B)
- 并行：`asyncio.create_task` 西医/中医/舌诊 → 顺序 await（真并发），synthesis+prescription 串行在后 → (A) 设计正确
- Accept/Edit/Reject：**不支持**，feedback 端点只审计 → (B)
- 模块状态追踪：`panel_status` dict + `AIRunStatus`（SUCCEEDED/PARTIAL/FAILED），模块失败永不抛 → (D) 健壮
- 结果缓存 `ai_cache_key` + `_safe_json`/`_strip_fence`（处理 Qwen3 思考链泄漏）→ (D)

## 5. Model Gateway `services/model-gateway/servers/`

| Server | Port | 状态 |
|---|---|---|
| llm_server.py（Qwen3.8-27B / ShizhenGPT，role flag，enable_thinking=False） | 7870/7871 | (D) |
| embedding_server.py（Qwen3-Embedding-8B dim4096） | 7873 | (D) |
| reranker_server.py（FlagReranker 单对打分） | 7874 | (D) |
| ocr_server.py（transformers AutoModel） | 7875 | (D) degraded→VLM 兜底 |
| asr_server.py | 7872 | (C) 源码注释自认 placeholder |

## 6. Knowledge `services/knowledge/rag.py`

- 返回字段含 source_id/title/excerpt/source_type/trust_tier/relevance_score/chapter/version/knowledge_version/domain → (D)
- 但前端**不显示 chapter/version** → (B)
- 无上传/启停/版本 UI（后端有端点）→ (C)
- 向量+关键词融合+rerank+降级 → (D)

## 7. Prescription

- `check_allergy`/`check_duplicate` 定义在 `followup/rules.py` **但处方流程从不调用** → (B)
- 过敏/相互作用/重复：仅 prompt 指示，落为 free-text `safety_notes` → (B) 需结构化 per-drug 告警
- `in_hospital_dict`：prompt 让模型设，前端渲染徽章，但**服务端不校验** → (B)

## 8. Followup

- 与 Encounter 关联：表有 FK，但 `/api/followups` 不存、前端硬编码 `followup-005` → (B)
- `assess()` 返回 risk_level + reason + recommended_action + escalation → (D)
- 患者时间线：无 → (C)

## 9. MockHIS `packages/shared/his_adapter.py`

- Protocol 契约 + 写入字段（doctor_id/encounter_id/signature_id/request_id/timestamp）→ (D)
- `get_medical_history` 实现了但 seed 不填 `_medical_records`，永远返回 `[]` → (C)
- 无时间线聚合，每患者仅 1 次 encounter → (C)

## 10. Seed `scripts/seed_demo.py`

- 5 患者×1 encounter，无纵向历史 → 不达"2-3 年纵向"产品化标准 → (C)/(B)
- 19 药品 / 3 协定方 / 6 知识文档 → (A) 覆盖度
- doctors 定义为常量但**不写 users 表** → (C)

---

## 跨切面产品化缺口（按严重度）

1. **持久化断层（最大）**：22 表全空，全靠 MockHIS 内存字典重启即丢。必须接 repository 层。
2. **无 streaming**：2-5 分钟阻塞 POST，需 SSE/async-job（POST 返 job_id → GET stream）。
3. **Accept/Edit/Reject 未接**：feedback 端点是审计 stub。
4. **无 auth**：`doctor_id` 硬编码，sign 无密码确认（违背 #67）。
5. **无纵向数据**：5 患者×1 encounter，阻断患者时间线。
6. **安全检查仅 prompt**：`check_allergy`/`check_duplicate` 存在但从未调用。
7. **知识管理 UI 缺失**：上传端点存在无 UI。
8. **seed 重复**：`ENC_SEED` 在前端 .tsx 与 seed_demo.py 双份，易漂移。
9. **`apps/admin-web`/`apps/patient-web` 空目录**（已广告但不存在）。
10. **ASR server 自认 placeholder**。
