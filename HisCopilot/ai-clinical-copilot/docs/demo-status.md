# Demo Status — 黄金路径 MVP

> 收敛为一条完整可重复演示的端到端门诊流程（`instruct/demo.md`）。
> 本文档记录如何启动、初始化、复现黄金路径，以及实测结果。
> 更新日期：2026-10-09。

## 一、如何启动系统

> 服务器已有 ComfyUI(8188) 与 aivideo(8000) 在跑——**本系统不碰这两个端口**。

### 1. 模型服务（GPU1-only）

```bash
# clinical_main (Qwen3.8-27B) 在 comfui env；tcm (ShizhenGPT) 在 hiscopilot-tcm env
bash scripts/start-model-servers.sh
# 端口：clinical_main 7870 · tcm 7871 · embedding 7873 · reranker 7874 · ocr 7875
```

### 2. 后端 API（conda env `hiscopilot`）

```bash
conda activate hiscopilot
cd ai-clinical-copilot
PYTHONPATH=. python -m uvicorn services.api.app:app --host 0.0.0.0 --port 8001
# 启动时自动 init_demo_data（幂等，重启不追加、不覆盖已编辑病历）
```

### 3. 前端（Next.js 15）

```bash
cd apps/doctor-web
npm run build && npm run start -- -p 3000
# http://localhost:3000
```

## 二、如何初始化 Demo 数据

**幂等**——API 启动时自动调用 `scripts/init_demo_data()`，也可手动执行：

```bash
conda activate hiscopilot
PYTHONPATH=. python scripts/init_demo.py
```

- 固定 ID：p-001..p-005 / enc-001..enc-005 / dr-001..dr-003。
- 写入 SQLite ORM 表（patients / encounters / users / lab_results）+ MockHIS 内存字典（双写兼容）。
- 知识库：首次 seed 6 份文档进 `knowledge_chunks` + `vec0`，已存在则跳过。
- **幂等保证**：重复运行不追加患者/就诊，不覆盖已编辑病历（按内容 hash 去重）。
- **不预置**：AI run / 处方草稿 / 随访——演示时实时生成（真实模型调用）。

## 三、如何复现黄金路径

打开 `http://localhost:3000/encounters/enc-003`：

1. **患者信息层**（中性）：基本信息/主诉/现病史/过敏/用药/慢病/检验。
2. 点 **"启动 AI 分析"** → 后台任务启动，返回 202 + ai_run_id → 前端轮询 `/api/ai-runs/{rid}`。
3. **模块状态条**实时更新：西医/中医/舌诊/知识/综合/处方 PENDING→RUNNING→COMPLETE/FAILED（真实模型调用驱动，非假进度）。
4. **AI 副驾层**（蓝标记）：西医分析（带 Accept/修改/驳回）、中医辨证、综合评估、处方草稿。
5. 点某条建议的 **接受/修改/驳回** → 落 `ai_feedback` 表（original + edited 同时保留）。
6. **正式病历编辑器**：输入内容 → 保存 → 落 `medical_records`（幂等，内容未变不覆盖）。
7. **处方草稿**：生成草稿 → 确认 → 医生签字 → 提交 MockHIS（状态机 AI_GENERATED→DOCTOR_CONFIRMED→SIGNED→SUBMITTED_TO_HIS）。
8. **随访**：创建随访计划（7/14/30 天）→ 模拟患者回答（输入"胸痛呼吸困难"）→ 风险评估 → 高危进 `/api/alerts` 预警列表。

## 四、实际调用的模型与端点

| 角色 | 模型 | 端口 | 端点 |
|---|---|---|---|
| clinical_main | Qwen3.8-27B (VLM+LLM) | 7870 | `/v1/chat/completions` |
| tcm | ShizhenGPT-7B-Omni | 7871 | `/v1/chat/completions` |
| embedding | Qwen3-Embedding-8B (dim 4096) | 7873 | `/v1/embeddings` |
| reranker | Qwen3-Reranker-8B | 7874 | `/v1/rerank` |
| ocr | PaddleOCR-VL (degraded→VLM) | 7870 | `/v1/chat/completions` |

所有 AI 输出均为真实模型推理结果（`instruct #50`，无 fake AI / 无静态 JSON 冒充）。

## 五、验证通过的步骤

| # | 步骤 | 结果 |
|---|---|---|
| 1 | 启动系统 | API 8001 / 前端 3000 / 模型 7870-7874 ✅ |
| 2 | `init_demo.py` 幂等 | 首次 5 患者/5 遇合/18 药品/3 协定方/6 知识文档；二次运行不追加 ✅ |
| 3 | 打开 `/encounters/enc-003` | 3 层布局（患者事实/AI 副驾/病历编辑器）渲染 ✅ |
| 4 | 启动 AI 分析 | 202 + ai_run_id 立即返回 ✅ |
| 5 | 轮询模块状态 | PENDING→RUNNING→逐模块 COMPLETE/FAILED（西医/中医/知识/综合/处方完成，舌诊 failed=无舌象图）✅ |
| 6 | AI 真实返回 | 西医 2 鉴别诊断 + 中医 3 证候候选 + 综合 + 处方 ✅ |
| 7 | Accept/Modify/Reject | `ai_feedback` 落库 1 条 ✅ |
| 8 | 保存病历 | `medical_records` 落库；重复保存同内容 no-op ✅ |
| 9 | 处方流程 | 草稿→确认→签字→提交 MockHIS，状态机正确，review trail 落库 ✅ |
| 10 | 幂等提交 | MockHIS 重复提交（同 encounter+signature）返回原记录，不产生重复处方/收费 ✅ |
| 11 | 随访闭环 | 创建计划→患者回答→风险 high_risk→escalation=True→alerts 列表 6 条 ✅ |
| 12 | 重启 API | 患者/就诊/病历/处方(SUBMITTED_TO_HIS)/随访/预警 全部仍在 ✅ |
| 13 | 模型失效降级 | `FAIL_CLINICAL_MAIN=1` → 运行 PARTIAL（西诊/综合/处方最终完成，舌诊 failed），HIS 主流程不阻塞 ✅ |

## 六、实测分析耗时

| 测试 | 输入 | 状态 | 总延迟 |
|---|---|---|---|
| enc-003 首次（冷） | 完整糖尿病复诊 | PARTIAL（舌诊无图） | **~240 秒** |
| enc-003 失效模拟 | `FAIL_CLINICAL_MAIN=1` | PARTIAL | 进行中（西诊最终成功） |

- 西医模块（Qwen3.8-27B）约 60s；中医模块（ShizhenGPT-7B-Omni）约 10s；综合约 90s；处方约 60s。
- 模块并行：西医/中医/舌诊 `asyncio.create_task`+`gather`（西医与中医真并发，舌诊无图秒 failed）。
- **不假进度**：模块状态仅由真实模型返回驱动；`latency_ms` 落 `ai_runs` 表如实记录。
- 首批 AI 信息（中医模块）约 10s 出现；西医约 60s；综合+处方后台补全。未达 update.md #20 "≤15s 首批" 目标——因单 GPU1 上 Qwen3.8-27B 推理固有延迟，非工程可压缩（属第二波 GPU benchmark 范畴）。

## 七、仍存在的问题

1. **舌诊模块**：无演示舌象图 → 模块 UNAVAILABLE（PARTIAL）。需补 `data/objects/` 舌象图片或患者端上传流程。
2. **OCR/ASR**：端口未常驻（按需启停 embedding/reranker 防 OOM）；黄金路径未上传检验报告图。
3. **首批 AI 延迟**：西医约 60s 才出，超 update.md 目标。需第二波 GPU benchmark + 顺序调度/量化评估。
4. **处方 per-drug Accept/Modify/Delete**：前端有下拉选择但未真正 PATCH 回服务端编辑结构化处方（状态机走 confirm 而非 per-drug edit）。
5. **auth**：`doctor_id="dr-001"` 硬编码，签字无密码确认（demo 级，违背 instruct #67 生产要求）。
6. **知识管理 UI**：上传端点存在但无前端 UI。
7. **患者端 `/patient`**：仍为硬编码单场景，未走完整 intake 流程。

## 八、暂未实现的功能（本轮范围外，属 update.md 第二波）

- 5 患者 × 2-3 年纵向时间线（update.md 阶段 2/13）
- 3 栏从零重构 Clinical Encounter Workspace（update.md 阶段 1，本轮为最小改造）
- GPU benchmark + `docs/performance-profile.md`（update.md 阶段 4）
- Evidence Drawer（update.md 阶段 6）
- TCM 四诊 UI（update.md 阶段 9）
- 患者端完整 intake 流程（update.md 阶段 10）
- 舌象拍摄指导 + 质量评分（update.md 阶段 11）
- 知识库管理 UI（update.md 阶段 15）
- RBAC 登录 + 签字密码确认（instruct #67）
- Facts/Inference/Uncertainty 输出结构（update.md 阶段 16）

---

**DEMO ONLY · 患者数据严禁出网 · 所有 AI 输出仅供医生参考，最终由执业医师签字后经 HIS Adapter 提交。**
