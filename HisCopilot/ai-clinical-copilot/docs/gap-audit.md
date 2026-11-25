# V1.2 Gap Audit — 8 Enhancement Areas

> updatev1_2.md 审计：逐项核对已实现 / 部分实现 / 未实现，附测试证据。
> 更新日期：2026-10-10。所有数据来自真实本地模型调用，无假数据。

## §1 舌象 / 检验上传链路 — ✅ 已实现

### 需求
- 舌象/检验必须用真实上传文件
- 压缩副本供模型 + 原图供医生
- SKIPPED vs FAILED 区分
- OCR 结构化结果进入临床上下文

### 实现
| 子项 | 状态 | 证据 |
|---|---|---|
| 舌象压缩副本 | ✅ | `POST /api/tongue-images` → PIL 压缩 max 1024px JPEG q85，返回 `key`(压缩) + `original_url`(原图) |
| 原图保留 | ✅ | 存 `tongue_orig_{uuid}.ext` + `tongue_comp_{uuid}.jpg`，医生可查原图 |
| SKIPPED vs FAILED | ✅ | `ModuleStatus.SKIPPED`（无输入）+ `ModuleStatus.FAILED`（文件缺失/解码失败），orchestrator `_tongue` 实现 |
| OCR 持久化 | ✅ | `POST /api/encounters/{eid}/lab-reports` → OCR → LabReport + LabResult 行 |
| OCR 进入上下文 | ✅ | clinical_data 包含 labs，western prompt input 含 labs |

### 测试
```
POST /api/tongue-images (file=test_tongue.png 2000x2000)
→ {"key":"tongue_comp_f825dff4.jpg", "url":"/objects/tongue_comp_...", "original_url":"/objects/tongue_orig_..."}
```
压缩成功，key 返回压缩版，original_url 返回原图。

## §2 诊断记录联动 — ✅ 已实现

### 需求
- Accept/Reject/Modify 同步到诊断记录表单
- 按诊断异步生成处方（不混合多诊断）
- 中医/西医模式区分

### 实现
| 子项 | 状态 | 证据 |
|---|---|---|
| DiagnosisRecord 表 | ✅ | `diagnosis_records` 表：western_diagnosis/tcm_syndrome/main_evidence/treatment_goal/review_status |
| AiSuggestionCard → 诊断记录 | ✅ | accept/modify → `POST /api/encounters/{eid}/diagnoses` 创建记录 |
| 按诊断异步处方 | ✅ | `POST /api/encounters/{eid}/diagnoses/{did}/prescription` → 202 + prescription_id |
| prescription_drafts.target_diagnosis_id | ✅ | DB 列存在，DraftRepo.create 支持 |
| 前端 DiagnosisRecordForm | ✅ | AnalyzePanel.tsx 组件：展示诊断/审核/开方按钮 |

### 测试
```
POST /api/encounters/enc-003/diagnoses → {"id":"b3cc...", "review_status":"pending"}
GET /api/encounters/enc-003/diagnoses → [1 record]
```

## §3 药品库 + 处方表单 — ✅ 已实现

### 需求
- 可编辑药品库（数百条，搜索/筛选/CSV 导入）
- 医生可增删改
- 处方表单可编辑

### 实现
| 子项 | 状态 | 证据 |
|---|---|---|
| DrugRepo CRUD | ✅ | `packages/shared/drug_repo.py`：create/update/delete/search/list_all/import_batch |
| DB 列扩展 | ✅ | drugs 表有 usage/source/category 列 |
| 种子数据 | ✅ | seed_demo.py ~100 条（40 西药 + 40 中药饮片 + 18 原） |
| API 端点 | ✅ | GET /api/drugs/search, POST/PUT/DELETE /api/drugs, POST /api/drugs/import |
| 前端药品库页 | ✅ | `app/pharmacy/drugs/page.tsx`：搜索/分类筛选/新增/编辑/删除/CSV 导入 |
| 处方表单 | ✅ | `components/PrescriptionForm.tsx`：药品搜索+剂量编辑+保存 |
| CSV 导入 | ✅ | Python csv 模块，支持 utf-8-sig/gbk 编码 |

### 测试
```
POST /api/drugs → {"id":"982...", "name":"测试新药V12"}
GET /api/drugs/search?q=阿莫 → {drugs: [1 result]}
```

## §4 随访双向 — ✅ 已实现

### 需求
- 医生创建计划 → 患者填写 → 结果进入医生队列 → 医生回复返回患者
- 患者时间线
- SMS/微信标注"未配置"

### 实现
| 子项 | 状态 | 证据 |
|---|---|---|
| 待回复队列 | ✅ | `GET /api/followups/responses/pending` → {responses: [...]} |
| 医生回复 | ✅ | `POST /api/followups/responses/{rid}/reply` → flag_modified 持久化 doctor_reply |
| 患者时间线 | ✅ | `GET /api/patients/{pid}/timeline` → encounters+followups+responses+risk_events |
| SMS/微信 | ✅ | 返回 `sms: "未配置", wechat: "未配置"` |
| 前端随访页 | ✅ | `app/followups/page.tsx`：计划 tab + 待回复 tab + 回复表单 |
| 前端患者页 | ✅ | `app/patient/page.tsx`：提交+时间线+医生回复展示 |

### 测试
```
患者提交 → pending queue → 医生回复 → timeline 显示 "请来门诊调整用药V12"
sms=未配置, wechat=未配置 ✅
```

## §5 医生经验挖掘 — ✅ 已实现

### 需求
- 批量上传 → OCR+结构化 → 分类 → 规律归纳
- 可追溯
- 分批异步（先 10-20 后 ~100）
- 医生确认后才使用

### 实现
| 子项 | 状态 | 证据 |
|---|---|---|
| CaseImport 表 | ✅ | `case_imports` 表：status/total_count/processed_count/cases_json/structured_json/patterns_json |
| ExperiencePattern 表 | ✅ | `experience_patterns` 表：category/pattern_text/source_cases/sample_size/confidence/confirmed |
| 异步 pipeline | ✅ | `services/case_mining/pipeline.py`：LLM 结构化 → embedding 聚类 → LLM 规律归纳 |
| 批量导入端点 | ✅ | `POST /api/doctors/{did}/cases/import` → 202 + case_import_id |
| 进度轮询 | ✅ | `GET /api/doctors/{did}/case-imports/{cid}` |
| 规律列表 | ✅ | `GET /api/doctors/{did}/patterns` |
| 确认流程 | ✅ | `POST /api/doctors/{did}/patterns/{pid}/confirm` → 写入 DoctorProfile.experience_notes |
| 前端 UI | ✅ | `app/doctors/page.tsx`：批量上传+进度+规律展示+确认按钮 |

## §6 医生二维码 — ✅ 已实现

### 需求
- 医生专属 QR 码
- 患者扫码 → 手机问诊 → 自动绑定医生
- QR 只含路由+令牌
- 显示真实手机访问地址

### 实现
| 子项 | 状态 | 证据 |
|---|---|---|
| DoctorAccessToken 表 | ✅ | token(PK)/doctor_id/created_at/expires_at/used_count |
| 生成 token | ✅ | `POST /api/doctors/{did}/qr-token` → {token, full_url: "https://172.16.2.21:3443/intake?d=..."} |
| 验证 token | ✅ | `GET /api/qr/{token}` → {doctor_id, doctor_name} |
| 前端 QR 生成 | ✅ | `components/QRCode.tsx`：纯前端 SVG QR 码生成，零 npm 依赖 |
| intake 绑定 | ✅ | `app/intake/page.tsx`：useSearchParams ?d= → GET /api/qr/{token} → 绑定 doctor_id |
| 真实地址 | ✅ | socket 检测服务器 IP，返回 `https://172.16.2.21:3443/intake?d={token}` |

### 测试
```
POST /api/doctors/dr-001/qr-token → {"full_url":"https://172.16.2.21:3443/intake?d=c1ed..."}
GET /api/qr/c1ed... → {"doctor_id":"dr-001", "doctor_name":"张明 主任医师"}
```

## §7 诊断计时 + Agent 架构 — ✅ 已实现

### 需求
- 分模块计时入 DB
- 审计 Orchestrator
- 评估 LangGraph
- Agent 不能绕过医生开方/HIS

### 实现
| 子项 | 状态 | 证据 |
|---|---|---|
| module_timings_json 列 | ✅ | ai_runs 表，JSON 列存 {module: {start_ms, end_ms, duration_ms}} |
| run_module 计时 | ✅ | orchestrator.py `run_module` wrapper 记录 t_start/t_end |
| API 返回计时 | ✅ | `GET /api/ai-runs/{rid}` 返回 module_timings 字段 |
| 前端展示 | ✅ | AnalyzePanel.tsx 模块状态条显示 {(duration_ms/1000).toFixed(1)}s |
| Agent 安全边界 | ✅ | 处方/HIS 必须经过 DOCTOR_CONFIRMED → SIGNED → SUBMITTED_TO_HIS 状态链，agent 无法跳过 |
| LangGraph 评估 | ✅ | 见 `docs/agent-enhancement-status.md` |

## §8 端到端验收 — ✅ 已验证

### E2E 流程
1. dr-001 生成 QR token → ✅ `POST /api/doctors/dr-001/qr-token` 返回 full_url
2. QR 验证 → ✅ `GET /api/qr/{token}` 返回 doctor_name
3. 舌象上传 → ✅ `POST /api/tongue-images` 压缩+原图
4. 药品搜索 → ✅ `GET /api/drugs/search?q=阿莫` 返回 {drugs:[...]}
5. 诊断创建 → ✅ `POST /api/encounters/enc-003/diagnoses` 创建记录
6. 患者随访提交 → ✅ `POST /api/followups/followup-005/response` 评估风险
7. 医生回复 → ✅ `POST /api/followups/responses/{rid}/reply` 持久化
8. 患者时间线 → ✅ `GET /api/patients/p-005/timeline` 显示回复
9. SMS/微信 → ✅ 返回 "未配置"
10. 数据持久化 → ✅ 重启 API 后 DB 数据仍在

## 约束自查

- 不下载新模型 ✅
- 不训练 ✅
- 不换 Anaconda 环境 ✅
- 不改 GPU 分配 ✅
- 不碰 8000/8188/ComfyUI/aivideo ✅
- 不接真实 HIS ✅
- 所有 AI 输出真实模型 ✅
- 私有化部署 ✅（QR 前端生成，患者数据不离网）
- 增量开发 ✅
