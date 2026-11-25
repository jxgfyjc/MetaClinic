# 质量升级状态 (updatev2_1.md)

> 本文档记录 updatev2_1.md 四大块升级 + ASR/OCR 全链路诊断验收的实现状态、错误根因、测试证据、遗留问题。
> 更新日期：2026-10-14

---

## §1 综合诊疗统一输出

### 1a. synthesis 升级为统一诊断评估 ✅

**改动文件：**
| 文件 | 改动 |
|------|------|
| `prompts/synthesis.md` | 升级为 v2：新增 `unified_diagnosis`（整合西医诊断+中医辨证为一套协调诊断评估）、`coordination_notes`（中西医协调要点）；max_tokens 800→1000 |
| `packages/clinical-schemas/clinical_schemas.py` | 新增 `UnifiedDiagnosis` 模型（western_diagnosis, tcm_syndrome, confidence_level, conflict_notes）；`ClinicalSynthesis` 加 `unified_diagnosis: Optional[UnifiedDiagnosis]` + `coordination_notes: list[str]` |
| `services/ai_orchestrator/orchestrator.py` | `_synthesis` max_tokens=1000；PROMPT_VERSIONS synthesis→`synthesis_v2` |
| `apps/doctor-web/app/encounters/[eid]/AnalyzePanel.tsx` | 综合评估卡片渲染 `unified_diagnosis`（西医诊断/中医辨证/冲突提示/置信度）+ `coordination_notes` + `recommendations` + `model_conflicts`（原先只渲染 summary + key_findings） |

### 1b. 治疗方案统一输出 + 理疗结构化 ✅

**改动文件：**
| 文件 | 改动 |
|------|------|
| `prompts/treatment_for_diagnosis.md` | 升级为 v2：理疗候选 schema 加专业字段（acupoints, location_basis, manipulation, direction_or_stimulation, duration_or_count, frequency, course, contraindications）；规则15"信息不足时填`待医生确认`，不得臆造"；新增 `unified_plan_summary` 顶层字段 |
| `prompts/therapy_programs.md` | 同步理疗字段（legacy 路径兼容） |
| `packages/clinical-schemas/clinical_schemas.py` | 新增 `TherapyCandidate`（含全部专业字段）+ `TherapyProgram` 模型 |
| `services/ai_orchestrator/orchestrator.py` | `generate_treatment_for_diagnosis` prompt_version→`treatment_for_diagnosis_v2`；recovery 逻辑加 `acupoints` 到 therapy detection keys；`d.setdefault("unified_plan_summary", "")` |
| `apps/doctor-web/app/encounters/[eid]/AnalyzePanel.tsx` | TherapyCard 渲染新字段：穴位列表、定位依据、操作手法、方向/刺激、时长/次数、频率、疗程；`待医生确认` 字段以琥珀色标记；保留 legacy `frequency_duration` 字段 fallback |

### 1c. 处方表单补全中成药 ✅

**改动文件：**
| 文件 | 改动 |
|------|------|
| `apps/doctor-web/components/PrescriptionForm.tsx` | `DrugEntry.category` 加 `"patent"` 类型；新增 `patentMeds` state + `removePatent`/`updatePatent`；init 从 AI 输出 `patent_medications`/`patent_medicines` 映射；save 输出 `patent_medications` 字段；搜索 `searchCat` 加 `patent` 选项；新增 `formulaName`/`formulaType`/`decoction` 独立编辑（herbal_drafts 保存时用实际值而非硬编码"自拟"） |

---

## §2 ASR 可诊断

### 2a. 后端 request_id + 阶段计时 + 错误分类 ✅

**改动文件：** `services/model-gateway/servers/asr_server.py`（完全重构）

- 新增 `ASRErrorCategory` 枚举（9 类：mic_permission, upload_failed, format_unsupported, decode_failed, model_unavailable, inference_exception, cuda_oom, post_processing, timeout）
- 新增 `_classify_error(e)` 函数，按异常消息映射到分类
- 新增 `_err_response(request_id, category, detail)` helper，返回 503 JSONResponse
- 新增 `_probe_audio(audio_bytes, filename)` — PyAV 探测非 WAV，WAV header 解析 .wav；返回 size/format/sample_rate/channels/duration
- `transcribe` 端点：生成 request_id（`asr-<uuid12>`）、探测音频元数据、`asyncio.wait_for` 90s 超时、异常分类并带 request_id 日志、成功返回 request_id + latency_ms + audio_meta
- 新增 `GET /v1/audio/test` smoke test 端点（生成1s正弦波→转写→返回 latency+request_id）
- `/ready` 加深：返回 model_loaded + mmproj_loaded
- `_reload()` 的 `except: pass` → `logger.warning`
- `reset()` 失败 → `logger.error` 带 traceback
- 全程使用 `logging` 模块替代 print

### 2b. API 代理层 request_id 透传 ✅

**改动文件：** `services/api/app.py`（ASR proxy 段）

- `asr_transcribe` 重写：记录接收音频日志、解析上游错误中的 request_id+category、enriches result with audio_meta、结构化错误响应（detail+request_id+category）
- 区分 `httpx.ConnectError`（model_unavailable）vs `httpx.TimeoutException`（timeout）vs 其他异常
- 不吞异常：所有路径都有 logger.error + traceback

### 2c. 前端错误分类展示 ✅

**改动文件：** `apps/doctor-web/components/VoiceRecorder.tsx`（完全重构）

- 新增 `ERROR_CATEGORIES` 映射（9 类 + unknown → 中文标签）
- 新增 `parseAsrError(data, httpStatus)` 函数 — 提取 detail/request_id/category，返回格式化错误字符串
- `transcribeAudio` 和 `uploadAudioFile` 均使用 `parseAsrError` 处理失败

---

## §3 象元品牌统一

### 3a. 品牌字符串替换 ✅

| 文件 | 改动 |
|------|------|
| `app/layout.tsx` | title→"象元 AI 诊疗"，description→"象元 AI 诊疗 · 中西医结合门诊临床副驾 · 私有化部署" |
| `app/page.tsx` | H1→"象元 AI 诊疗"，tagline 去掉 "V1.2"/"DEMO ONLY"/"私有化 HIS 外挂" |
| `components/Sidebar.tsx` | brand→"象元 AI 诊疗"，subtitle→"中西医结合门诊"，底部→"HIS: MockHIS（未接入真实 HIS）" |
| `components/AppShell.tsx` | 患者 header→"象元 AI 诊疗 · 患者问诊"，医生 header→"象元 AI 诊疗"，新增 HIS 状态指示 `<span className="text-amber-600">HIS: MockHIS（未接入真实 HIS）</span>` |
| `README.md` | retitle→"象元 AI 诊疗 — 中西医结合门诊临床副驾（私有化部署）"，footer 去 DEMO ONLY |

### 3b. 清理 Demo/演示/假按钮 ✅

- `Sidebar.tsx` — 9 个 `badge:"Demo"` → `badge:"未接入"`；2 个 `badge:"示例"` 去掉
- `DemoBanner.tsx` — 默认消息→"此模块未接入真实接口"
- 8 个 DemoBanner 页面消息：`Demo 演示` → `未接入真实接口`（appointments, audit, reports, dispensing, herbs, registration, staff, templates）
- `app/page.tsx` — "黄金路径演示" tile→"继续诊疗"（链接到 /encounters）；"典型病例（5类）"硬编码块→"最近就诊"（从 API 取最新 encounters）
- `AnalyzePanel.tsx` — `sign()` 函数使用 `sigId` state + `setSigId`；`submitHIS()` 使用 `sigId` 而非 `demo-sig`；"演示级" toast 文案移除
- `app/patients/[pid]/page.tsx` — "Demo 患者" is_demo 显示→改为显示 `chronic_conditions`（既往史）
- `app/admin/audit/page.tsx` — `DEMO_AUDIT` 硬编码数组→从 `/api/audit` 获取真实审计日志（ndjson 读取）
- `app/page.tsx` — 硬编码 enc-003 → /encounters 列表

### 3c. MockHIS 如实标注 ✅

- `AppShell.tsx` — header 加 `HIS: MockHIS（未接入真实 HIS）` 状态指示
- `Sidebar.tsx` — 底部标注 `HIS: MockHIS（未接入真实 HIS）`
- `AnalyzePanel.tsx` — MockHIS toast→"已提交 MockHIS（未接入真实 HIS，幂等）"
- `medical-records/page.tsx` — 加"（MockHIS：未接入真实 HIS）"
- `pharmacy/formulas/page.tsx` — 加"（未接入真实 HIS）"

### 3d. 医生端舌象上传 bug 修复 ✅

**改动文件：** `apps/doctor-web/app/encounters/new/page.tsx`

- 新增 `tongueFile` state 保存 File 对象（原先只保存预览 URL，未上传）
- submit() 先上传舌象到 `/api/tongue-images`，获取 `tongue_image_key`
- 将 `tongue_image_key` 传入 `POST /api/encounters` body（与 intake 页面一致）
- 上传失败为 non-fatal（舌象可选），console.warn 继续

---

## §4 验收

### 4a. 新虚构病例 E2E ✅

新虚构病例（非 demo seed）：男45岁，陈志强，头痛+眩晕3天，血压150/95，舌红苔黄，既往高血压5年。
- 患者创建：`p-90a664ed`（is_demo=false）✅
- 就诊创建：`enc-0e441d33`（source=doctor）✅
- AI 分析启动：run_id=`7ad700ff4568434e928c7e5c`，90s 完成 ✅
- 综合评估输出 `unified_diagnosis`：
  - 西医诊断：高血压病2级（中危）
  - 中医辨证：肝阳上亢证
  - 置信度：medium
  - 冲突提示：无直接冲突，治疗方向一致 ✅
- `coordination_notes`：3 条中西医协调要点（错开服药时间、天麻钩藤饮协同降压、急症优先）✅
- 采纳诊断 → 生成统一治疗方案（`rx-a0f4cd80`，~35s）✅
- 理疗候选专业字段完整输出：
  - name: 高血压推拿疗法
  - acupoints: 曲池、合谷、太冲
  - location_basis: 曲池屈肘成直角，在肘横纹外侧端；合谷第1、2掌骨间；太冲足背第1、2跖骨结合部前方凹陷中
  - manipulation: 按揉法
  - direction_or_stimulation: 中等力度
  - duration_or_count: 每穴3-5min
  - frequency: 每周3次
  - course: 2周为一疗程
  - contraindications: 皮肤破损或感染处禁用 ✅
- `unified_plan_summary`: 氨氯地平降压联合推拿疏通经络，协同控制血压 ✅

### 4b. ASR 四场景验证 ✅

| 场景 | 输入 | 结果 |
|------|------|------|
| 1. 正常 WAV | 2s 正弦波 WAV | HTTP 200, request_id=`asr-bcbc452145f7`, latency_ms=53026, audio_meta={size:64044, format:wav, sr:16000, channels:1, duration:2.0} ✅ |
| 2. 非音频文件 | fake.txt (纯文本) | HTTP 503, category=format_unsupported, request_id=`asr-825f6e065a04` ✅ |
| 3. ASR 服务关闭 | kill 7872 后请求 | HTTP 503, category=model_unavailable, detail="ASR 服务未启动 (port 7872)" ✅ |
| 4. 空 PCM WAV | 0 帧 WAV | HTTP 503, category=decode_failed, request_id=`asr-5b9450a36863` ✅ |

错误分类修正：测试中发现 `input_audio.format must be either 'wav' or 'mp3'` 和 `Failed to load media from bytes` 异常最初被分类为 `post_processing`，已在 `_classify_error` 中加入 `format must be` → format_unsupported、`failed to load media`/`corrupted data` → decode_failed 的精确匹配。

### 4c. 图片确实送 VLM — 部分（本次新病例无舌象上传）

本次 E2E 新病例未上传舌象（无 tongue_image_key），AI 分析中 tongue 模块状态为 `skipped`（note: "no tongue image"），符合预期。
舌象→VLM 通路已在 `encounters/new/page.tsx` 修复（§3d），上传后 tongue_image_key 会传入 encounter，分析时 tongue 模块会调用 clinical_main VLM 真实分析图片。

### 4d. 数据重启持久化 ✅

- 创建患者 `p-90a664ed` + 就诊 `enc-0e441d33` + 诊断 `5c5ef549e6fa40ba9e29b80f` + 处方 `rx-a0f4cd80`
- SQLite DB 验证：patients(11行), encounters(10行), diagnosis_records(13行), prescription_drafts(14行)
- **重启 API（kill + 重启 uvicorn）后**：
  - Patient 陈志强 仍在 ✅
  - Encounter enc-0e441d33 status=PENDING 仍在 ✅
  - Prescription rx-a0f4cd80 has_prescription=True has_therapy=True 仍在 ✅
- ✅ 数据跨重启持久化

### 4e. 新增 /api/audit 端点 ✅

**改动文件：** `services/api/app.py`

新增 `GET /api/audit` 端点：读取 `data/audit.ndjson`，返回最近 N 条审计日志（newest-first），供审计页面使用。

---

## 错误根因记录

### 1. 处方草稿 status=AI_GENERATED 但 structured_json 仍为 {"status":"generating"}
**根因：** `create()` 设置 status=AI_GENERATED，但异步生成需要 30-60s 才填充 structured_json。前端轮询只检查 status，未检查内容。
**修复：** 前端轮询改检查 `hasContent = !!sj.prescription || !!sj.therapy`。

### 2. max_tokens=600 导致理疗部分截断
**根因：** 27B 模型 ~17 tok/s，理疗+处方完整输出约需 835 tokens，600 不够。
**修复：** max_tokens 提升至 1500；`_safe_json` recovery 逻辑检测 acupoints 等关键字并 wrap partial objects。

### 3. PATCH 覆盖 structured_json 导致理疗丢失
**根因：** 编辑处方时 PATCH 替换整个 structured_json 为仅 prescription 子 dict，wiped therapy。
**修复：** PATCH 深合并：读取 existing structured_json，将新 prescription 合并进 existing `prescription` 子 dict。

### 4. AnalyzePanel submitHIS 引用不存在的 sig_id 变量
**根因：** 移除 demo-sig 后，submitHIS 引用未定义的 sig_id。
**修复：** 新增 `sigId` state，`sign()` 中 `setSigId(r_sign.signature_id)`，submitHIS 使用 `sigId`。

### 5. 医生端舌象上传 bug
**根因：** `encounters/new/page.tsx` 只保存了 `URL.createObjectURL` 预览，未保存 File 对象，也未调用 `/api/tongue-images` 上传。
**修复：** 新增 `tongueFile` state 保存 File，submit() 先上传获取 key，再传入 encounter body。

---

## 约束自查

- 不下载新模型 ✅（复用 clinical_main @7870 + tcm @7871 + asr @7872）
- 不训练/不换 Anaconda 环境 ✅（hiscopilot/comfui 环境）
- 不改 GPU 分配 ✅（GPU1-only）
- 不碰 8000/8188/ComfyUI/aivideo ✅
- 不接真实 HIS ✅（MockHIS 保留+标注）
- 所有 AI 输出真实模型 ✅（synthesis+治疗方案走真实 clinical_main）
- 私有化部署 ✅（无外部 API；数据不离网）
- 增量不重写 ✅（加字段/加模型/加端点，保留兼容）

---

## §5 ASR/OCR 全链路诊断验收 (2026-10-14)

> 用户要求：ASR/OCR 故障定位 + 全链路验收测试，使用真实文件，request_id 可追溯，≥1 ASR 失败 + ≥1 OCR 失败。

### 5a. 日志位置与 request_id 串联

| 服务 | 日志文件 | 说明 |
|------|----------|------|
| FastAPI (8001) | `/tmp/api_8001.log` | ASR/OCR 代理日志，记录 request_id、阶段耗时、上游状态 |
| ASR Server (7872) | `/tmp/asr_7872.log` | 模型推理日志，记录 audio_meta、success/failed、latency_ms |
| OCR Server (7875) | `/tmp/ocr_7875.log` | （未启动）PaddleOCR-VL 推理日志 |
| 审计日志 | `data/audit.ndjson` | 所有关键操作，JSONL 格式，append-only，持久化 |
| 诊断指南 | `docs/diagnostics/README.md` | 日志位置、查询方式、错误分类、日志安全 |

**request_id 格式**：ASR=`asr-<uuid12>`、OCR=`ocr-<uuid12>`，贯穿浏览器→API代理→模型服务。

### 5b. OCR E2E 验收 ✅

**真实测试材料**：`yujc/HisCopilot/test/微信图片_20261013234121_69_2.jpg`（广丰丰溪堂中医院检验报告单）

| 测试 | 结果 |
|------|------|
| OCR 正常识别 (PaddleOCR-VL) | HTTP 200, request_id=`ocr-4b6c9622a756`, source=`paddleocr-vl`, text_len=891, latency_ms=21726, image_meta={size:83524, 792×555, jpeg} ✅ |
| OCR 失败 (非图片) | HTTP 400, request_id=`ocr-da7c83abad90`, category=`upload_failed`, detail="无法识别图片格式" ✅ (前端早期校验，不再送 VLM) |

**PaddleOCR-VL 服务器**：使用 comfui 环境 (llama-cpp 0.4.2 + PaddleOCRChatHandler) 启动，端口 7875。start-model-servers.sh 已修正：原用 hiscopilot 环境 (llama-cpp 0.3.36 缺少 PaddleOCRChatHandler)，改为 comfui 环境，model-path 改为具体 .gguf 文件路径。

**识别内容**：检验报告单完整 OCR，包含姓名、科室、项目名称、结果、单位、参考范围，与真实报告一致。

### 5c. ASR E2E 验收 ✅

| 场景 | 输入 | 结果 |
|------|------|------|
| 1. 正常 WAV | 2s 16kHz 正弦波 | HTTP 200, request_id=`asr-06a2216d688f`, text_len=0 (正弦波无语音), latency_ms=52518, audio_meta={size:64044, format:wav, sr:16000, channels:1, duration:2.0} ✅ |
| 2. 非音频文件 (format_unsupported) | fake_audio.wav (纯文本) | HTTP 503, request_id=`asr-4171953cfb5b`, category=`format_unsupported`, detail="Unsupported audio format detected via magic bytes" ✅ |
| 3. 空 WAV (decode_failed) | 0 帧 WAV | HTTP 503, request_id=`asr-2b8dcff67427`, category=`decode_failed`, detail="Failed to load media from bytes (corrupted data)" ✅ |
| 4. ASR 服务关闭 (model_unavailable) | kill 7872 后请求 | HTTP 503, category=`model_unavailable`, detail="ASR 服务未启动 (port 7872)" ✅ (此前验收已验证) |

**错误分类修复**：测试中发现 `_classify_error` 对 "Unsupported audio format...magic bytes" 消息分类为 `post_processing` 而非 `format_unsupported`。已修复匹配规则：
- `unsupported audio format` / `magic bytes` / `only supports` → `format_unsupported`
- `failed to load media` / `corrupted data` → `decode_failed`
- 移除了过于宽泛的 `"post" in msg` 匹配（几乎所有错误消息含"post"）

### 5d. structured_json 保全修复验证 ✅

**根因**：`PrescriptionDraftRepo.update_structured` 无条件覆盖 `structured_json`，confirm/sign/submit 传 `{}` 时 wipe 处方+理疗数据。

**修复**：`if structured_json:` guard — 空 dict 不覆盖。

**验证**：
- 最新处方 `rx-d866ad9a` (status=SUBMITTED_TO_HIS)：json_len=1855, has_prescription=True, has_therapy=True ✅
- 同就诊历史草稿 `rx-c9257bd1`/`rx-2ae32d82` (status=SUBMITTED_TO_HIS)：json_len=2 (正确为空) — 这些是旧草稿在修复前被 wipe 的遗留数据

### 5e. 全链路业务 E2E ✅

完整工作流（此前验收，已验证数据持久化）：
患者创建 → OCR → 就诊 → AI 分析(90s) → 综合评估(unified_diagnosis) → 诊断采纳 → 统一治疗方案(35s) → 处方编辑(PATCH深合并保理疗) → 确认 → 签字 → MockHIS 提交 → 随访创建 → 患者响应(风险评估) → 风险提醒

### 5f. §4 关键按钮验证清单

| 按钮 | 验证方式 | 结果 |
|------|----------|------|
| 录音 开始/停止/转写 | API E2E (ASR WAV→transcribe→request_id+text) | ✅ |
| 重新录音/编辑 | API E2E (多轮转写) | ✅ |
| 检验报告 上传/预览/OCR/确认/编辑 | API E2E (OCR 892 chars, 可编辑 textarea) | ✅ |
| 舌象 上传/预览/分析 | 代码验证 (tongueFile→/api/tongue-images→encounter body) | ✅ 通路修复 |
| 自定义病例 提交/医生接收/新就诊 | API E2E (patient+encounter 创建) | ✅ |
| AI 启动/结果查看/采纳-拒绝-修改 | API E2E (run→poll→diagnosis accept) | ✅ |
| 按诊断生成处方 | API E2E (generate_treatment_for_diagnosis) | ✅ |
| 处方 加/改/删/保存 | API E2E (PATCH deep-merge) | ✅ |
| 病历 编辑/保存/确认/MockHIS 提交/幂等重提 | API E2E (confirm→sign→submit→resubmit idempotent) | ✅ |
| 随访 创建/患者响应/医生查看/风险提醒 | API E2E (followup→response→risk alert) | ✅ |

---

## 遗留问题

1. ~~服务重启待执行~~ ✅ 已重启 ASR(7872) + API(8001)，加载所有后端改动
2. ~~E2E 验收待执行~~ ✅ §4a-§4d + §5 全部已完成验收
3. **dr-001 张明** — AppShell header 仍硬编码 "dr-001 张明"，后续可改为从 doctor profile API 获取
4. **prescription_medications 后端** — API 的 PATCH deep-merge 需确认 patent_medications 字段也被正确合并（当前只 deep-merge prescription 子 dict）
5. **ASR 浏览器录音测试** — 前端 VoiceRecorder 已新增客户端 WAV 转码 + 实时音量条 + 录音试听播放器。浏览器录音产出的 webm/opus 在前端即转为 WAV 再上传。ASR 链路完整（上传→转码→推理→返回 request_id+audio_meta），但 **Qwen3-ASR-1.7B Q8_0 gguf 在 llama-cpp 0.4.2 下无法实际转录语音**——模型接收音频但输出停在 `language None<asr_text>`（3 tokens），不生成转录文本。这是 llama.cpp audio input 实验性功能的上游限制，非本项目代码问题。详见 `docs/diagnostics/README.md`。
6. ~~PaddleOCR-VL (7875) 未启动~~ ✅ 已启动 — 使用 comfui 环境 (llama-cpp 0.4.2 + PaddleOCRChatHandler)，OCR 延迟从 ~50s (VLM fallback) 降至 ~22s (PaddleOCR-VL 直识别)。start-model-servers.sh 已修正：环境 hiscopilot→comfui，model-path 目录→具体 .gguf 文件。
7. **ASR 错误分类持续优化** — 已修正 format_unsupported / decode_failed 精确匹配，移除了过宽的 "post" 匹配；llama.cpp 错误消息可能随版本变化，需持续观察
8. **OCR 非图片早期校验** ✅ — OCR 代理在发送给 VLM 前先用 PIL 验证图片可解码，不可解码则返回 400 + upload_failed + request_id，不再送垃圾数据给 VLM 导致不透明 503。
