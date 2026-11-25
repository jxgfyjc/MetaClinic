你现在不要继续以“添加功能”的方式开发。

当前项目已经完成：

* FastAPI
* Next.js
* Qwen3.8-27B
* ShizhenGPT-7B-Omni
* ASR/OCR 基础能力
* AI Orchestrator
* RAG 基础能力
* Mock HIS
* 处方审核/签名
* 随访
* 审计
* AI Failure graceful degradation
* E2E Sandbox

当前状态属于：

> Functional Prototype / P1

现在进入：

> Phase 2 — Productized Clinical MVP / P2

本阶段的目标不是增加更多功能，而是把现有系统从“工程 Demo”提升成“看起来像真实医院临床软件、医生真的愿意使用”的产品。

---

# 第一原则

不要推倒重写现有后端。

首先完整审计当前 repository：

* 页面
* API
* 数据模型
* AI workflow
* model gateway
* knowledge
* prescription
* followup
* mock HIS

识别：

1. 哪些可以保留
2. 哪些需要重构
3. 哪些只是 Demo placeholder
4. 哪些是真正可复用的业务逻辑

创建：

docs/productization-audit.md

---

# 第一阶段：重做医生工作台

这是整个 Phase 2 的最高优先级。

医生进入：

/encounters/[eid]

不要再把页面设计成“AI结果展示页”。

它应该成为：

> Clinical Encounter Workspace

目标：

医生进入患者页面后，10 秒内理解患者。

布局：

左侧：

Patient Context

* 患者基本信息
* 年龄/性别
* 过敏
* 既往史
* 当前用药
* 慢病
* 关键检验
* 最近诊疗时间线

中间：

Clinical Workspace

* 主诉
* 现病史
* 既往史
* 体征
* 检验
* 舌象
* 正式病历编辑器

右侧：

AI Copilot

顶部状态：

* AI Ready
* AI Analyzing
* Partial
* Failed

AI区域：

[病例摘要]

[西医分析]

[中医辨证]

[舌象]

[处方建议]

[随访建议]

不要使用 ChatGPT 风格聊天界面作为主 UI。

---

# 第二阶段：患者纵向时间线

建立：

Patient Timeline

展示：

* 就诊
* 诊断
* 检验
* 药物
* 随访
* 舌象
* AI建议
* 医生最终诊断

例如：

2025-09
门诊

2025-11
HbA1c 7.2%

2026-01
药物调整

2026-03
HbA1c 7.8%

2026-03-12
本次咳嗽

要求使用真实 demo seed 数据构建长期纵向病例。

不要只提供单次 Encounter。

---

# 第三阶段：AI 分析改成 Streaming / Progressive Results

当前存在严重问题：

enc-003 AI 分析约 260 秒。

这是不可接受的交互形式。

不要让：

POST /analyze

等待全部 AI 完成后一次返回。

重构为：

Start AI Run
→ return ai_run_id

然后前端通过：

SSE / WebSocket / polling

获取每个模块状态。

状态：

WESTERN
PENDING → RUNNING → COMPLETE

TCM
PENDING → RUNNING → COMPLETE

TONGUE
PENDING → RUNNING → COMPLETE

KNOWLEDGE
PENDING → RUNNING → COMPLETE

SYNTHESIS
PENDING → RUNNING → COMPLETE

处方建议可以在分析基础完成后异步生成。

---

# 第四阶段：不要让“asyncio parallel”误认为是真正并行

当前 Qwen3.8-27B 和 Shizhen 都在 GPU1。

检查：

nvidia-smi

分析：

* VRAM
* compute utilization
* latency
* KV cache
* model contention

建立：

docs/performance-profile.md

进行至少三组 benchmark：

A:
Western only

B:
TCM only

C:
Western + TCM concurrent

记录：

* first token latency
* completion latency
* total latency
* peak VRAM

如果 concurrent latency 明显恶化，不要强行并发。

可以：

1. sequential scheduling
2. quantization
3. background execution
4. smaller TCM inference
5. cache
6. precompute

最终目标：

医生进入患者页面后立即看到已有患者信息；

10 秒左右得到第一批 AI 信息；

其余 AI 在后台持续补全。

绝对不要追求“所有 AI 必须一次性同时完成”。

---

# 第五阶段：AI 输出不再只是 JSON Card

所有重要 AI 输出必须做 Clinical UI。

例如鉴别诊断：

Diagnosis Candidate

支持证据：

* 症状 A
* 检验 B

反对证据：

* 没有发热
* ...

信息缺口：

* 尚未获得胸部影像

然后提供：

[查看依据]

[加入病历]

[忽略]

---

# 第六阶段：建立 Evidence Drawer

医生点击：

为什么？

打开右侧 Evidence Drawer。

显示：

来源：

《某医学指南》

章节：

XXX

证据：

原文摘要

来源等级：

Official Guideline

或者：

《伤寒论》

来源：

TCM Classic

或者：

院内专家经验

来源：

呼吸科 / 专家经验 #E102

必须显示：

* source
* source type
* title
* chapter
* version
* knowledge tier

---

# 第七阶段：建立 AI → Doctor 协作

任何 AI 建议都必须具有：

Accept
Edit
Reject

例如：

AI：

考虑支气管炎

医生：

Edit

修改：

“急性支气管炎可能性较高，需排除肺炎”

记录：

original
edited
doctor
timestamp

建立：

AI Feedback

数据库继续保留。

---

# 第八阶段：重做处方工作流

当前不是简单：

AI Prescription → Sign

而应该：

AI Prescription Draft

↓

Doctor Review

↓

每一项药物支持：

保留
修改
删除

药物卡显示：

药品名称
规格
剂量
频次
疗程

以及：

Allergy Check
Interaction Check
Duplicate Check
Patient Context Warning

如果患者存在：

青霉素过敏

则对应药物明确显示：

⚠ Allergy Conflict

如果存在肾功能相关风险：

⚠ Renal Function Consideration

---

# 第九阶段：中医方案界面

中医不要只显示：

证型 1
证型 2
证型 3

要展示：

四诊信息

望：
舌象

闻：
暂无

问：
症状

切：
暂无

然后：

证候候选

每一个候选：

证候名称
支持证据
矛盾信息
置信度
参考依据

然后：

治法

经方候选

院内协定方

AI 自主组合

三者必须明确区分。

---

# 第十阶段：患者端彻底产品化

当前 /patient 不应只是简单 demo。

患者流程：

开始问诊

↓

选择：

语音
文字

↓

实时转写

↓

AI自动整理

↓

患者确认：

“以下内容是否准确？”

↓

上传：

检查报告

↓

OCR

↓

检查结果确认

↓

上传：

舌象

↓

图片质量检查

↓

拍摄指导

↓

完成

最终：

“资料已提交给医生”

---

# 第十一阶段：舌象页面

不要只是上传图片。

需要：

拍摄指导：

* 光线
* 距离
* 舌头自然伸出
* 避免滤镜
* 保持清晰

图片质量评分：

Pass
Warn
Reject

然后：

舌体 ROI

舌色

苔色

厚薄

腻

裂纹

齿痕

所有结果标记：

Observed
Not observed
Uncertain

不能让视觉模型凭空补充没有观察到的特征。

---

# 第十二阶段：随访必须和 Encounter 连起来

就诊完成后：

AI 自动生成：

Follow-up Recommendation

医生可以：

[创建随访计划]

例如：

7天
14天
30天

患者之后收到：

症状
用药
不良反应
指标

随访回答：

↓

AI 摘要

↓

Risk Classification

↓

医生工作台：

“待处理随访”

高危：

立即升级

所有随访记录形成 Patient Timeline。

---

# 第十三阶段：建立真实 Demo Patient

不要再只用一次性病例。

至少准备 5 个长期 Demo 患者。

每人：

2~3 年虚拟就诊数据

10~30 条检验结果

5~10 条药物记录

3~5 次门诊

2~5 次随访

1~3 张舌象历史

必须全部明确：

DEMO DATA / NOT REAL PATIENT

这样 UI 才有真实医院的数据密度。

---

# 第十四阶段：首页不要再做普通 Dashboard

医生首页应该突出：

今日需要处理的事情：

待接诊
待审核 AI
待签名
待处方
待随访
高危预警

而不是：

CPU
GPU
模型数量
AI调用量

系统监控信息只放管理员界面。

---

# 第十五阶段：知识库必须产品化

知识库页面：

左侧：

西医
中医
经典
方剂
协定方
药品
专家经验

中间：

文档列表

右侧：

内容预览

支持：

上传
解析
版本
启用
停用

并显示：

来源
作者
更新时间
知识等级

---

# 第十六阶段：不要为了“医疗安全”让模型变成拒答机器

保留：

模型自主推理

但要求：

事实和推理分开。

输出：

Facts

Inference

Uncertainty

Evidence

Recommendation

而不是大量：

“请咨询专业医生”。

本产品本身就是面向医生使用的临床辅助系统。

---

# 第十七阶段：AI Result Schema

重新检查：

WesternAnalysis
TCMAnalysis
TongueAnalysis
Synthesis
PrescriptionDraft
FollowupRisk

所有字段都必须真正服务于 UI。

不要有大量没人展示、没人使用的 JSON 字段。

---

# 第十八阶段：视觉设计

整个产品不能像 AI SaaS 模板。

目标：

> Private Hospital Clinical Workstation

风格：

* 白色
* 浅灰
* 医疗蓝/青
* 高信息密度
* 细边框
* 少卡片
* 少圆角
* 清楚的信息层级
* 表格/时间线/临床标签

不要：

* 紫色 AI 渐变
* 巨大 Hero
* 大量营销文案
* ChatGPT 风格气泡
* 大面积卡片
* 花哨动画

---

# 第十九阶段：所有页面进行一次真实产品 Review

逐页面检查：

/

patients

/encounters/[eid]

/knowledge

/patient

/admin

问：

这个页面是谁用？

他进入这个页面的目的是什么？

第一眼看什么？

下一步做什么？

如果没有答案，删掉或者重构。

---

# 第二十阶段：验收指标

不要用：

“功能完成”

作为完成标准。

使用：

### UX

医生打开患者：

≤ 10 秒理解患者

### AI

第一批 AI 信息：

目标 ≤ 15 秒

全部 AI：

后台继续完成

### Data

患者必须有长期时间线

### Evidence

AI主要判断必须可追溯到来源

### Collaboration

AI必须支持：

Accept / Edit / Reject

### Prescription

AI草稿和正式处方必须明确分离

### Followup

就诊 → 随访 → 风险 → 医生处理必须贯通

### Failure

任何 AI 模块失败：

HIS主流程继续

---

# 第二十一阶段：绝对禁止

本阶段禁止：

* 再增加大量新功能
* 再下载新模型
* 进行模型训练
* 修改 ComfyUI
* 修改 8000/8188
* 重写整个 backend
* 用 mock AI 冒充真实 AI
* 用静态 JSON 假装实时 AI
* 为了视觉效果引入无关组件

---

# 最终目标

当我打开：

/encounters/enc-003

我不应该感觉：

“这是一个 AI Demo。”

而应该感觉：

> “这是一个医院医生正在使用的临床工作站，只不过右边多了一位 AI 副驾。”

医生看到的是：

患者历史
+
当前问题
+
检查
+
AI分析
+
知识依据
+
处方草稿
+
随访计划

整个体验应该围绕：

> **一次真实临床接诊**

展开。

完成后创建：

docs/productization-status.md

记录：

* Before
* After
* UX changes
* Performance changes
* AI workflow changes
* data model changes
* remaining limitations
