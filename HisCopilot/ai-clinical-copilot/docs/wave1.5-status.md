# Wave 1.5 Status — 性能优化与多医生个性化诊疗

> instruct_1.md Wave 1.5 交付文档。记录性能优化前后数据、多医生个性化方案示例、
> 综合治疗方案实现、实际通过的测试及未解决的问题。
> 更新日期：2026-10-09。所有 AI 输出均来自真实本地模型调用。

## 一、执行顺序与完成状态

| 阶段 | instruct_1.md 章节 | 状态 |
|---|---|---|
| 1. 性能测量与优化 | §2 | ✅ 完成 |
| 2. 多医生个性化诊疗 | §3 | ✅ 完成 |
| 3. 综合治疗方案 | §4 | ✅ 完成 |
| 4. 工作台体验优化 | §5 | ✅ 完成 |
| 5. 回归测试 + 交付 | §6 | ✅ 完成 |

## 二、性能优化（§2）

### 优化前 → 优化后

| 指标 | Before (v1.0) | After (v1.1+并发+slim) | 改善 |
|---|---|---|---|
| E2E 总耗时（enc-003 冷运行） | ~240s | **~227s** | **-5%**（单次基准 -22%） |
| western 完成 | ~107s | ~86-91s | -15% |
| synthesis+prescription wall | ~138s（串行） | ~106s（并发） | -23% |
| prescription 输出 | ~2538 chars | ~1877-2624 chars | 精简 |
| western 输出 | ~2021 chars | ~1858 chars | -8% |
| synthesis 输出 | ~420 chars | ~401 chars | -5% |

### 优化措施

1. **synthesis ↔ prescription 并发化**：原先串行（~138s），现 `asyncio.create_task` 并发（~106s wall）。单 GPU1 时间片共享，overlap 隐藏排队延迟。
2. **下游 prompt 上下文精简**：`_slim_western` / `_slim_tcm` / `_slim_patient` 去除冗长数组（supporting_evidence/contradicting_evidence 等），仅传诊断依据摘要给 synthesis/prescription。**不截断存储输出**——完整结构化输出照常落 `ai_outputs` 表并展示给医生。
3. **prompt 文本精简（v1.0→v1.1）**：去除冗长装饰文本与重复 "Input you will receive" 段，增加明确 conciseness 指令。`PROMPT_VERSIONS` + cache_key 同步更新。
4. **LLM 超时提升**：clinical_main 调用 timeout 120s→300s，避免长输出 ReadTimeout 导致整个 run FAILED。

### 剩余瓶颈

- **prescription（~70-80s wall）**：最大瓶颈，必须生成完整西药+中成药+中药+安全检查 JSON，属 Qwen3.8-27B 在 GPU1 上的固有推理延迟。
- **单 GPU1 时间片共享**：clinical_main 同时跑 western + synthesis + prescription + therapy（4 次调用），无法真正并行。
- **首批 AI 延迟**：tcm ~21s，western ~86-91s，超 update.md #20 "≤15s" 目标——属第二波 GPU benchmark 范畴。

详见 `docs/performance-profile.md`。

## 三、多医生个性化诊疗（§3）

### 实现方式

- **不为每位医生训练独立模型**。沿用 Qwen3.8-27B (clinical_main) + ShizhenGPT-7B-Omni (tcm)。
- 新增 `DoctorProfile` ORM 表 + `DoctorProfileRepo`：科室/擅长/辨证偏好/经方协定方偏好/组方加减偏好/非药物组合偏好/分析重点/个人经验条目。
- 医生画像通过 `ClinicalContext.doctor_profile` 注入各模块 prompt：
  - `_western`：`doctor_focus`（分析重点 + 辨证偏好）影响分析侧重。
  - `_tcm`：`doctor_preference`（辨证/经方/组方偏好）影响证候候选排序与方药选择。
  - `_prescription`：`doctor_preference`（经方/组方/非药物偏好）影响候选方案。
  - `_therapy`：`doctor_preference` 影响治疗项目排序与筛选。
- **硬约束**：偏好影响候选排序/分析重点/呈现方式，但**不代替患者实际病情，不覆盖过敏、禁忌和重大风险检查**。
- **cache_key 包含 doctor_id**：不同医生的缓存隔离，dr-001 的缓存不会给 dr-002。

### 两份医生画像

| 属性 | dr-001 张明 | dr-002 李秀英 |
|---|---|---|
| 科室 | 全科/中西医结合 | 中医科 |
| 擅长 | 糖尿病、中西医结合慢病管理 | 中医内科、针灸推拿 |
| 辨证偏好 | 重视气阴两虚证，优先益气养阴法 | 注重阴虚燥热与瘀血并见证，活血化瘀贯穿全程 |
| 经方偏好 | 偏好经典经方（生脉散、玉泉丸）加减，少用大方 | 偏好滋阴润燥方（增液汤、白虎加人参汤）合活血方 |
| 组方偏好 | 组方精简，药味精简（8-12味） | 常加丹参、川芎活血；12-16味，气血同治 |
| 非药物偏好 | 重视生活方式干预，配合推拿疏通经络 | 首选针灸通络，配合艾灸足三里；推拿用于下肢麻木 |
| 分析重点 | 西医指标优先，中医辨证辅助；注重并发症筛查 | 中医辨证为主，西医指标参考；注重经络评估 |

### 同一病例（enc-003 糖尿病复诊）对比 — 差异来自配置而非写死

**dr-001 输出：**
- 中药：[classic] **生脉散合玉泉丸加减**（人参、麦冬、五味子、天花粉、葛根、黄芪、山药、苍术）— 益气养阴法
- 中药：[hospital] 降糖对药方加减
- 西药：格列美脲片 + 达格列净片 + 甲钴胺片
- 治疗项目排序：**推拿优先** → 理疗 → 康复训练 → 艾灸 → 针灸

**dr-002 输出：**
- 中药：[ai_generated] **增液汤合补阳还五汤加减**（生地黄、麦冬、玄参、黄芪、当归、川芎、丹参、赤芍）— 滋阴润燥+活血化瘀
- 中药：[ai_generated] **白虎加人参汤合桃红四物汤加减**（石膏、知母、人参、甘草、桃仁、红花、当归、川芎）— 清热+活血化瘀
- 西药：格列美脲片 + 甲钴胺片 + **普瑞巴林**（神经痛药）
- 治疗项目排序：**针灸优先** → 艾灸 → 推拿 → 理疗 → 康复训练

**差异确认**：
1. 中药方剂不同：dr-001 用 classic 经方（生脉散合玉泉丸，益气养阴）；dr-002 用 ai_generated 自主组方（增液汤合补阳还五汤+白虎加人参汤合桃红四物汤，滋阴润燥+活血化瘀）——**明确反映 dr-002 的活血化瘀偏好**。
2. formula_type 不同：dr-001 = classic+hospital；dr-002 = ai_generated（更自主的组方）。
3. 治疗项目排序不同：dr-001 推拿优先；dr-002 针灸+艾灸优先——**反映非药物组合偏好差异**。
4. 西药略有差异：dr-002 加了普瑞巴林（针对神经病变疼痛）。

## 四、综合治疗方案（§4）

### 独立数据结构

新增 `TherapyProgram` ORM 表 + `TherapyProgramRepo`，**独立于协定方库**（Formula 表）。

字段：项目名称 / category(推拿/针灸/艾灸/理疗/康复训练) / 适用场景 / 治疗目的 / 频次时长 / 禁忌 / 院内项目编码 / source_type(hospital/knowledge)。

### 来源区分

| source_type | 含义 | hospital_code |
|---|---|---|
| `hospital` | 院内现有项目 | 有真实编码（如 TCM-TN-001） |
| `knowledge` | 知识库/教科书方案 | 空 |
| `ai_generated` | AI 自主建议（运行时生成） | 空，不存储为目录行 |

**不伪造收费编码**：只有 hospital source_type 携带 hospital_code，AI 自主建议永远无编码。

### 院内目录（5 项，seed 进 DB）

| 编码 | 类别 | 项目 |
|---|---|---|
| TCM-TN-001 | 推拿 | 糖尿病康复推拿 |
| TCM-ZJ-002 | 针灸 | 针灸通络治疗 |
| TCM-AJ-003 | 艾灸 | 足三里艾灸 |
| PT-ZP-004 | 理疗 | 中频理疗（糖尿病改善微循环） |
| RT-YD-005 | 康复训练 | 康复训练（糖尿病运动处方） |

### AI 治疗方案模块

新增 `_therapy` runner（复用 clinical_main，不新增模型）：接收患者上下文 + 西医/中医分析 + 院内目录 + 医生偏好，生成候选方案。与 synthesis/prescription 并发执行。

输出结构：`candidates[]`（含 category/name/source_type/hospital_code/applicable_scenario/therapeutic_goal/frequency_duration/contraindications/rationale）+ `lifestyle_advice[]` + `safety_notes[]`。

实测：5 个候选方案，均正确匹配院内项目并携带 hospital_code，source_type=hospital。

## 五、工作台体验（§5）

- **医生选择器**：AI 副驾区新增 doctor 下拉选择，切换医生后显示当前画像摘要（科室/擅长/辨证偏好/组方偏好）。同一病例可切换医生比较。
- **模块状态条**：7 模块（西医/中医/舌诊/知识/综合/处方/理疗方案）PENDING→RUNNING→COMPLETE/FAILED，真实模型调用驱动。
- **综合治疗方案区**：独立卡片展示治疗项目候选，分区于处方草稿。每项含 接受/修改/驳回/查看依据 按钮。来源徽章区分 院内现有/知识库/AI自主。
- **处方草稿**：per-drug Accept/Modify/Delete + 院内字典徽章 + 过敏冲突 + 重复用药警示 + 状态机（AI_GENERATED→DOCTOR_CONFIRMED→SIGNED→SUBMITTED_TO_HIS）。
- **AI 建议**：每条 Accept/Modify/Reject，original + edited 同时落 `ai_feedback` 表。
- **病历编辑器**：幂等保存，不覆盖已编辑版本。
- 患者信息层/历史/过敏/用药/检验清晰可见。无无关 Dashboard，无大规模 UI 重写。

## 六、回归测试（§6）

| # | 测试 | 结果 |
|---|---|---|
| 1 | 系统启动 | API 8001 / 前端 3000 / 模型 7870-7871 ✅ |
| 2 | init_demo 幂等 | doctor_profiles=2, therapy_programs=5 二次运行不追加 ✅ |
| 3 | 打开 /encounters/enc-003 | 3 层布局 + 医生选择器渲染 ✅ |
| 4 | 启动 AI 分析（dr-001） | 202 + ai_run_id，~227s PARTIAL（舌诊无图 failed） ✅ |
| 5 | 模块状态轮询 | 7 模块逐个 COMPLETE/FAILED，真实模型驱动 ✅ |
| 6 | therapy 模块 | 5 候选方案，全部 source_type=hospital + 正确 hospital_code ✅ |
| 7 | dr-002 同病例对比 | 中药方剂/治疗排序/西药差异确认（见 §三） ✅ |
| 8 | 处方流程 | 草稿→确认→签字→提交 MockHIS 状态机 + review trail ✅ |
| 9 | MockHIS 幂等 | 重复提交（同 encounter+signature）返回原记录 ✅ |
| 10 | 重启 API 持久化 | 13 ai_runs / 74 ai_outputs / 2 doctor_profiles / 5 therapy_programs / 1 prescription_draft 全部仍在 ✅ |
| 11 | 模型失效降级 | FAIL_CLINICAL_MAIN=1 → PARTIAL，HIS 主流程不阻塞（demo-status 已验证） ✅ |
| 12 | 缓存隔离 | dr-001 与 dr-002 cache_key 含 doctor_id，不串缓存 ✅ |

## 七、未解决的问题

1. **舌诊模块**：无演示舌象图 → UNAVAILABLE（PARTIAL）。需补 `data/objects/` 舌象图片或患者端上传流程。graceful UNAVAILABLE 是正确行为，不阻塞。
2. **首批 AI 延迟**：western ~86-91s，超 update.md #20 "≤15s" 目标。属第二波 GPU benchmark + 量化/多卡范畴（硬约束禁止下载新模型/训练/更换模型）。
3. **therapy Accept/Modify/Reject**：前端有按钮但未真正 PATCH 回服务端编辑治疗项目草稿（与处方 per-drug edit 同问题，demo 级）。
4. **auth**：`doctor_id` 由前端选择，签字无密码确认（demo 级，违背 instruct #67 生产要求）。
5. **knowledge source_type**：therapy 目前生成的候选均为 hospital（因院内目录已覆盖），knowledge/ai_generated 来源区分逻辑已实现但需更多非院内目录场景验证。
6. **模型服务器 usage tokens=0**：transformers 后端不填充 usage，无法用 token throughput 精确度量，以 wall-clock + 输出 char 数为准。

## 八、约束自查

- ✅ 不重写后端：orchestrator runner 逻辑不变，仅 `run()` 调度顺序 + 新增 `_therapy` + 下游 input 精简 + doctor_profile 注入。
- ✅ 不破坏 Golden Path：202+polling、状态机、MockHIS 幂等、随访闭环均不变。
- ✅ 不截断结果伪造性能：完整结构化输出照常存储展示。
- ✅ 不假进度：模块状态仍由真实模型 COMPLETE 驱动。
- ✅ 不碰 8000/8188、不碰 ComfyUI、GPU1-only、无新模型/训练/更换。
- ✅ 不接真实 HIS，不使用真实患者生产数据。
- ✅ 不伪造院内项目编码：只有 hospital source_type 携带 hospital_code。

---

**DEMO ONLY · 患者数据严禁出网 · 所有 AI 输出仅供医生参考，最终由执业医师签字后经 HIS Adapter 提交。**
