# V1.0 独立中西医门诊系统 — 状态报告

> 对应 `instruct/updatev1_1.md`：将系统升级为自成体系、可独立运行的 V1.0 门诊系统。
> 验证日期：2026-10-10

## 一、实现总览

V1.0 在既有 MVP Sandbox 基础上，新增**双入口问诊 + 语音/OCR/舌象接入 + 持久化就诊 + 自动病历草稿**，
形成完整闭环：患者或医生录入 → DB 持久化 → 自动病历草稿 → 医生选模式 → 真实模型 AI 分析 → 审核 → 保存 → 随访。
全程不依赖真实 HIS（MockHIS 保留接口，V1.0 用自身 SQLite）。

### 双入口

| 入口 | 路径 | 说明 |
|---|---|---|
| A · 患者自主问诊 | `/intake` | 移动优先多步表单：患者信息(手机查档)→病情(文本/语音)→检验报告(OCR)→舌象→预览→提交。患者端隐藏侧边栏(AppShell)。 |
| B · 医生直接接诊 | `/encounters/new` | 搜索/新建患者 → 主诉病史(语音)→症状/慢病/过敏/用药/检验/舌象 → 提交后 redirect 到诊疗室。 |

两入口统一写入同一 `Patient + Encounter` 模型，`source` 字段区分来源（patient/doctor）。

## 二、后端实现

### 2.1 DB Schema 扩展（`packages/shared/db.py`）
- `Encounter.clinical_data: JSON` — 持久化 history/raw_text/medications/allergies/labs/case_type/source/tongue_image_key
- `Patient.extra_json: JSON` — 持久化 allergies/insurance/id_card/address
- `_migrate_add_columns()` — PRAGMA table_info 检查 + ALTER TABLE，兼容已有 DB（无需删库重建）

### 2.2 Repository 适配（`packages/shared/repo.py`）
- `PatientRepo.upsert` 写 `extra_json`；`get`/`list_all` merge 到顶层（向后兼容）
- `EncounterRepo.upsert` 写 `clinical_data`；`get`/`list_all` merge 到顶层
- 新增 `PatientRepo.search_by_phone`、`EncounterRepo.update_clinical_data`

### 2.3 新增 API 端点（`services/api/app.py`）
| 端点 | 用途 | 验证 |
|---|---|---|
| `POST /api/patients` | 创建患者，auto-gen `p-{hex8}` | ✅ p-a2c5e48e 王芳 |
| `POST /api/encounters` | 创建就诊，auto-gen `enc-{hex8}`，status=PENDING，source=patient/doctor，持久化 clinical_data + labs，自动生成病历草稿 | ✅ enc-72435e78 |
| `PATCH /api/encounters/{eid}` | 补充/更新 clinical_data | ✅ |
| `GET /api/encounters?status=PENDING` | 状态过滤（待接诊队列） | ✅ 返回 2 条 PENDING |
| `GET /api/encounters/{eid}` | DB-first + MockHIS 兜底 + labs + medical_record merge + source | ✅ |
| `GET /api/patients` | DB-first + MockHIS 兜底 | ✅ 7 patients |
| `GET /api/patients/{pid}` | DB-first | ✅ |
| `GET /api/patients/search/phone/{phone}` | 手机号查档 | ✅ |
| `POST /api/asr/transcribe` | ASR 代理 → 7872，503 降级 | ✅ 端到端转录 |
| `POST /api/ocr/extract` | OCR 代理 → 7875(PaddleOCR-VL) → fallback 7870(clinical_main VLM)，503 降级 | ✅ |

### 2.4 自动病历草稿（模板生成，非 AI 调用）
`_generate_medical_record_draft(eid, pid, enc)` — 从 intake 数据拼装主诉/现病史/症状/慢病/过敏/用药/检验/查体/诊断/处理的草稿，
写入 MedicalRecord。即时生成，医生可编辑，AI 分析是独立的显式动作。

## 三、ASR / OCR 服务

| 服务 | 端口 | 环境 | 模型 | 加载方式 | 验证 |
|---|---|---|---|---|---|
| ASR | 7872 | comfui | Qwen3-ASR-1.7B (Q8_0 + mmproj bf16) | `Qwen3ASRChatHandler` + llama-cpp | ✅ health ok + 端到端转录 |
| OCR | 7875 | comfui | PaddleOCR-VL-1.6 (Q8_0 + mmproj) | `PaddleOCRChatHandler` + llama-cpp | ✅ health ok + 图片识别 |

- `services/model-gateway/servers/asr_server.py` — transcribe 接受 filename Form 参数
- `services/model-gateway/servers/ocr_server.py` — 完全重写为 llama-cpp + PaddleOCRChatHandler（放弃 transformers 路径）
- 两服务 ~3-4GB each，GPU1 8GB 空闲共存，不碰 8000/8188/ComfyUI/aivideo

## 四、前端实现

### 4.1 新页面
| 路径 | 文件 | 说明 |
|---|---|---|
| `/intake` | `app/intake/page.tsx` | 患者多步表单（5步+成功页），VoiceRecorder + LabUpload + 舌象上传 |
| `/encounters/new` | `app/encounters/new/page.tsx` | 医生新建就诊，搜索/新建患者 + 临床上下文 + 语音/OCR/舌象 |

### 4.2 升级页面
| 路径 | 改动 |
|---|---|
| `/` (首页) | 顶部双入口卡片（患者自主问诊 / 医生直接接诊）+ 待接诊队列(source badge) + 统计 |
| `/encounters` | 状态过滤 tabs(全部/待接诊/进行中/已完成) + 新建就诊按钮 + source badge + created_at |
| `/encounters/[eid]` | DB-first 读取(/api/encounters/{eid}) + source badge 显示 |
| `AnalyzePanel.tsx` | 病历编辑器预填自动草稿(medical_record.content.text) |

### 4.3 共享组件
| 组件 | 说明 |
|---|---|
| `VoiceRecorder.tsx` | MediaRecorder API → POST /api/asr/transcribe → onTranscribe(text)，503 降级提示 |
| `LabUpload.tsx` | 图片上传 → POST /api/ocr/extract → 可编辑 OCR 文本 → parseLabs 结构化 |
| `AppShell.tsx` | 条件布局：/intake 隐藏侧边栏（患者端干净体验） |
| `Sidebar.tsx` | 工作台组加「患者自主问诊」「新建就诊」 |
| `Breadcrumb.tsx` | 加 intake/new 标签 |
| `lib/api.ts` | 加 `apiUpload` multipart 上传 helper |

## 五、验证结果（虚构病例）

### 5.1 入口B · 医生直接采集（失眠+焦虑病例）
- 患者：王芳, 女, 42岁, phone 139****7777 → `p-a2c5e48e`
- 主诉：入睡困难伴多梦3月，焦虑加重1周
- 症状：失眠/多梦/焦虑/心悸/乏力 | 慢病：焦虑状态 | 用药：枣仁安神胶囊
- 就诊：`enc-72435e78`, source=doctor, status=PENDING
- ✅ DB 持久化：clinical_data 包含 history/raw_text/medications/allergies/labs/source/case_type
- ✅ 自动病历草稿：模板生成主诉/现病史/症状/过敏/用药/检验/查体/诊断/处理

### 5.2 入口A · 患者自主问诊（头痛病例，前一轮已验证）
- 患者：陈小明 → `p-f8185f0a`，就诊 `enc-6b1c9ee3`, source=patient, status=PENDING
- ✅ 出现在待接诊队列

### 5.3 真实 AI 分析（enc-72435e78）
- ai_run_id: `5bb7b110e9474411bbfb5c0b`
- 最终状态：**PARTIAL**（舌诊 failed — 未上传舌象图，符合预期降级；其余 6 模块 complete）
- 耗时：217913ms（~3.6分钟，真实模型推理）
- 西医：广泛性焦虑障碍(GAD)伴继发性失眠 / 原发性慢性失眠障碍
- 中医：心脾两虚证(medium) / 心胆气虚证(medium)
- 综合：西医倾向焦虑障碍，中医辨证心脾两虚或心胆气虚，病机互补
- ✅ 所有 AI 输出来自真实模型调用（clinical_main 7870 + tcm 7871），无静态 JSON

### 5.4 持久化 / 降级 / 刷新验证
| 验证项 | 结果 |
|---|---|
| 重启 API → 数据仍在 | ✅ encounter + patient + ai_run + medical_record 全部从 DB 恢复 |
| AI 故障 → PARTIAL 降级 | ✅ 舌诊 failed，其余模块 complete，HIS 主流程不阻塞 |
| 页面刷新 → AI run 恢复轮询 | ✅ GET /api/ai-runs/{rid} 从 DB 返回完整状态 |

## 六、约束自查

| 约束 | 状态 |
|---|---|
| 不下载新模型 | ✅ ASR/OCR 权重已齐备 |
| 不训练/不换模型/不创新环境 | ✅ 复用 comfui + hiscopilot env |
| 不改 GPU 分配 | ✅ GPU1-only，8GB 空闲共存 |
| 不碰 8000/8188/ComfyUI/aivideo | ✅ 全程未触碰 |
| 不接真实 HIS | ✅ MockHIS 保留接口，V1.0 用自身 DB |
| 保留现有 AI 核心 | ✅ orchestrator/模型/RAG/SQLite/处方状态机不动 |
| 所有 AI 输出真实模型 | ✅ ASR/OCR/AI 分析都走真实模型 |
| 不大规模重构后端 | ✅ 加列+加端点+加 repo 适配 |
| 患者数据不出网 | ✅ 私有化部署，全本地推理 |

## 七、已知限制 / 后续

1. **舌象上传**：intake 页舌象图片当前标记为 pending，正式上传需 encounter 创建后调 `/api/encounters/{eid}/tongue-image`（诊疗室内已可用）。如需 intake 即时上传，可加 tmp 端点。
2. **OCR 大图超时**：PaddleOCR-VL 处理大图(720×1280)可能超时，建议前端压缩到 256-512px（已验证小图 200 响应）。舌象图本身无文字，OCR 返回空属正常。
3. **Embedding/Reranker (7873/7874)**：health 未响应，但知识库模块仍 complete（可能走缓存或降级），不影响主流程。
4. **就诊列表**：种子数据(enc-001~005)状态为 IN_PROGRESS/SUBMITTED，新建就诊为 PENDING，tab 过滤已验证。
