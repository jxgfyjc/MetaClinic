# 你现在是这个项目的 Principal Engineer + AI Architect + Product Engineer

你要负责从当前服务器环境开始，直接 Vibe Coding 搭建一个：

> **私有化 HIS 外挂 AI 中西医门诊临床副驾系统**
>
> 核心定位：Clinical AI Copilot，而不是独立 HIS，也不是 AI 自动医生。
>
> 原有 HIS 永远是患者、就诊、收费、医嘱、处方等正式业务事实的主系统。
> 本系统作为 AI Sidecar / 外挂辅助层：
>
> 患者端采集信息 → 医疗信息结构化 → AI 并行分析 → 知识库检索 → AI 生成建议 → 医师审核/修改/签名 → 通过受控 Adapter 写回 HIS。
>
> AI 服务发生故障时，不允许阻塞原 HIS 正常业务。
>
> 当前目标不是直接生产上线，而是：
>
> **先做出一个真正可运行、前后端完整、模型真实调用、知识库真实检索、患者→医生→AI→审核→模拟 HIS→随访的端到端 MVP Sandbox。**
>
> 之后再接真实医院 HIS。

---

# 0. 最高优先级原则

严格遵守以下原则。

## 原则 1：先检查环境，再编码

不要假设：

* 模型安装在哪里
* GPU 编号是什么
* CUDA 版本是什么
* Python 版本是什么
* Docker 是否存在
* Hugging Face 是否缓存模型
* Qwen3.8-27B 的实际目录是什么
* ShizhenGPT 的实际目录是什么
* PaddleOCR 模型是否已经缓存

必须先检查。

首先执行：

```bash
pwd
uname -a
cat /etc/os-release || true

nvidia-smi
nvidia-smi -L
nvcc --version || true

python3 --version
python --version || true
pip --version || true

docker --version || true
docker compose version || true

df -h
free -h
lsblk || true

find /data -maxdepth 3 -type d 2>/dev/null | head -200
find /models -maxdepth 3 -type d 2>/dev/null | head -200
```

如果 `/data/models`、`/models` 等不存在，继续搜索：

```bash
find / -maxdepth 4 \
  \( -iname '*Qwen3*' \
  -o -iname '*Shizhen*' \
  -o -iname '*ASR*' \
  -o -iname '*Embedding*' \
  -o -iname '*Reranker*' \) \
  2>/dev/null | head -300
```

找到模型后，建立统一配置文件：

```text
infra/models.yaml
```

不要把绝对路径硬编码到业务代码里。

---

# 1. 当前硬件假设

当前服务器：

```text
GPU:
2 × NVIDIA A100 80GB

目标：
私有化部署
内网使用
中文医疗场景
中西医结合门诊
小型医院
当前缺少大量高质量院内训练数据
```

所以第一阶段：

**不做模型微调。**

采用：

```text
强通用模型
+
中医专用模型
+
RAG
+
Tools
+
结构化输出
+
规则校验
+
医生审核
```

不要浪费时间先训练 LoRA。

---

# 2. 当前已经下载好的模型

假设以下模型已经下载，但：

**绝对禁止假设路径。**

必须实际扫描并识别。

预期可能存在：

```text
Qwen3.8-27B
ShizhenGPT-7B-Omni
Qwen3-ASR-1.7B
Qwen3-Embedding-8B
Qwen3-Reranker-8B
PaddleOCR / PaddleOCR-VL
HuatuoGPT-o1-72B GGUF
```

程序最终统一抽象成：

```yaml
models:
  clinical_main:
    type: vlm_llm

  tcm:
    type: vlm_llm

  asr:
    type: asr

  embedding:
    type: embedding

  reranker:
    type: reranker

  ocr:
    type: ocr

  second_opinion:
    type: llm
```

实际路径、运行时、GPU 分配必须根据机器探测结果决定。

---

# 3. 模型角色

## Main Clinical Brain

使用：

```text
Qwen3.8-27B
```

负责：

* 病历总结
* 主诉/现病史整理
* 症状理解
* 西医病情分析
* 鉴别诊断候选
* 检验结果综合解释
* 多模态病例理解
* 综合临床推理
* 患者追问
* 随访分析
* 最终临床辅助意见

原则：

> 它是主力推理模型。

---

## TCM Brain

使用：

```text
ShizhenGPT-7B-Omni
```

负责：

* 中医问诊理解
* 望闻问切相关信息整合
* 舌象信息理解
* 证候候选
* 辨证依据
* 治法候选
* 经方/中医方案候选

原则：

> 不让主模型假装自己是专门的中医模型。

---

## ASR

使用：

```text
Qwen3-ASR-1.7B
```

负责：

```text
语音 → 时间轴文本
```

然后再交给 Main Clinical Brain：

```text
ASR
 ↓
Transcript
 ↓
Qwen3.8
 ↓
Structured Medical History
```

---

## OCR

采用：

```text
PaddleOCR / PaddleOCR-VL
```

负责：

* 检查报告
* 化验单
* 门诊报告
* PDF
* 图片
* 表格
* 检验数值

之后：

```text
OCR
 ↓
Structured OCR Result
 ↓
Qwen3.8
 ↓
医学语义归一化
```

---

## Embedding

使用：

```text
Qwen3-Embedding-8B
```

负责知识库向量检索。

---

## Reranker

使用：

```text
Qwen3-Reranker-8B
```

负责：

```text
Query
 ↓
Top 30~50 retrieval
 ↓
Reranker
 ↓
Top 5~10 evidence
```

---

## Second Opinion

如果本机存在：

```text
HuatuoGPT-o1-72B GGUF
```

先做成：

```text
optional_second_opinion
```

不要常驻。

只用于：

* 疑难病例
* 第二意见
* Benchmark
* 模型对比

第一阶段不要求其参与普通病例。

---

# 4. GPU 调度

默认目标：

```text
GPU 0:
Qwen3.8-27B

GPU 1:
ShizhenGPT
Embedding
Reranker
ASR
```

但是：

**如果实际显存、模型量化方式、runtime 证明其他分配更优，以实际测试结果为准。**

禁止直接假设。

建立：

```text
infra/gpu-profile.md
```

记录：

```text
GPU
显存
模型
显存占用
KV Cache
并发测试
平均响应时间
峰值响应时间
```

---

# 5. AI Runtime

优先检查：

```text
vLLM
```

是否能够正确运行各模型。

如果模型架构与 vLLM 不兼容：

* 查模型官方推荐 runtime
* 选择 SGLang / Transformers / llama.cpp / 专用 runtime
* 不要为了统一 runtime 强行修改模型

业务层统一成 OpenAI-compatible 风格：

```text
POST /v1/chat/completions
POST /v1/embeddings
```

或者内部 Adapter：

```python
LLMProvider
EmbeddingProvider
RerankerProvider
VisionProvider
ASRProvider
```

最终业务层绝对不能依赖具体模型 SDK。

---

# 6. 总体产品架构

目标：

```text
                       Patient Web
                            │
                            ▼
                    API Gateway / BFF
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
 Patient Service      Encounter Service     Followup
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
                  Clinical AI Orchestrator
                            │
        ┌───────────────────┼────────────────────┐
        │                   │                    │
        ▼                   ▼                    ▼
   Western Brain       TCM Brain           Vision/OCR/ASR
     Qwen3.8          ShizhenGPT              services
        │                   │                    │
        └───────────────────┼────────────────────┘
                            │
                     Evidence Layer
                            │
          ┌─────────────────┼──────────────────┐
          │                 │                  │
          ▼                 ▼                  ▼
      Hospital DB       Knowledge RAG      Drug DB
          │                 │                  │
          └─────────────────┼──────────────────┘
                            │
                            ▼
                  Clinical Recommendation
                            │
                            ▼
                     Doctor Workbench
                            │
                 ┌──────────┴──────────┐
                 │                     │
              Approve                Modify
                 │                     │
                 └──────────┬──────────┘
                            ▼
                       Doctor Sign
                            │
                            ▼
                       HIS Adapter
                            │
                            ▼
                     Mock HIS / Real HIS
```

---

# 7. MVP 阶段不要接真实 HIS

当前必须先实现：

```text
Mock HIS Adapter
```

模拟：

```text
Patient
Encounter
Doctor
Prescription
Medical Record
Billing
Pharmacy
```

接口设计必须与未来真实 HIS Adapter 解耦。

例如：

```python
class HISAdapter:
    async def get_patient(...)
    async def get_encounter(...)
    async def get_medical_history(...)
    async def submit_medical_record(...)
    async def create_prescription(...)
    async def get_drug_dictionary(...)
    async def get_formula_dictionary(...)
```

然后实现：

```text
MockHISAdapter
```

未来再添加：

```text
HospitalHISAdapter
```

---

# 8. 技术栈

优先采用：

## Frontend

```text
Next.js
React
TypeScript
TailwindCSS
shadcn/ui
```

要求：

* 医生桌面端优先
* 同时兼容平板
* 患者移动端优先
* 中文
* 医疗工作台风格
* 不做花哨 AI Chat UI
* 信息密度适中
* AI 建议必须明显区分于正式医疗记录

---

## Backend

```text
Python
FastAPI
Pydantic
SQLAlchemy
Alembic
```

---

## Database

```text
PostgreSQL
```

扩展：

```text
pgvector
```

第一阶段优先使用：

```text
PostgreSQL + pgvector
```

不要一开始就引入大量基础设施。

---

## Cache

```text
Redis
```

---

## File/Object Storage

```text
MinIO
```

用于：

```text
语音
舌象
面象
检查报告
OCR原图
生成文件
附件
```

---

## Search

第一版：

```text
PostgreSQL Full Text Search
+
pgvector
```

只有确实需要后再增加：

```text
OpenSearch
```

---

## Workflow

如果实现自动随访：

```text
Temporal
```

但可以先提供简单版：

```text
Followup Scheduler
```

MVP 打通后再接 Temporal。

---

# 9. 仓库结构

创建：

```text
ai-clinical-copilot/
│
├── apps/
│   ├── doctor-web/
│   ├── patient-web/
│   └── admin-web/
│
├── services/
│   ├── api/
│   ├── ai-orchestrator/
│   ├── model-gateway/
│   ├── asr/
│   ├── ocr/
│   ├── vision/
│   ├── knowledge/
│   ├── medication/
│   ├── followup/
│   ├── his-adapter/
│   └── audit/
│
├── packages/
│   ├── clinical-schemas/
│   ├── ui/
│   └── shared/
│
├── knowledge/
│   ├── tcm/
│   ├── western/
│   ├── formulas/
│   ├── herbs/
│   ├── hospital/
│   ├── drugs/
│   └── expert/
│
├── data/
│   ├── demo/
│   ├── imports/
│   └── processed/
│
├── infra/
│   ├── docker/
│   ├── compose/
│   ├── models.yaml
│   └── environment.yaml
│
├── scripts/
│   ├── detect_models.sh
│   ├── detect_gpu.sh
│   ├── healthcheck.sh
│   ├── seed_demo.sh
│   └── start-dev.sh
│
├── docs/
│   ├── architecture.md
│   ├── model-deployment.md
│   ├── api.md
│   ├── knowledge-base.md
│   ├── HIS-adapter.md
│   └── runbook.md
│
├── docker-compose.yml
├── .env.example
├── Makefile
└── README.md
```

---

# 10. 第一阶段一定要做到的完整用户流程

建立一个真实 Demo 患者。

例如：

```text
患者：
王某某
```

病例包括：

```text
主诉：
反复咳嗽约3周

现病史：
夜间明显，痰少，偶有胸闷

既往史：
糖尿病

过敏史：
青霉素过敏

用药史：
二甲双胍

检验：
HbA1c 7.8%
eGFR 48

舌象：
淡红舌、白苔、稍腻
```

Demo 数据只能作为测试数据。

不能冒充真实患者。

---

# 11. 患者端

页面：

```text
首页
我的就诊
今日问诊
上传检查报告
上传舌象
语音描述病情
文字描述病情
历史记录
随访
```

---

# 12. 患者语音流程

实现：

```text
点击开始录音
 ↓
浏览器录音
 ↓
ASR service
 ↓
Transcript
 ↓
LLM医学结构化
 ↓
展示给患者确认
 ↓
提交给医生
```

结构化输出：

```json
{
  "chief_complaint": "",
  "history_of_present_illness": "",
  "past_medical_history": [],
  "medication_history": [],
  "allergies": [],
  "family_history": [],
  "symptoms": [],
  "duration": "",
  "patient_raw_text": ""
}
```

禁止丢失原始文本。

必须同时保存：

```text
raw_audio
raw_transcript
structured_record
```

---

# 13. 检查报告 OCR

实现：

```text
上传图片
 ↓
OCR
 ↓
layout
 ↓
table recognition
 ↓
medical normalization
 ↓
结构化检查结果
```

数据库：

```text
LabResult
```

字段至少：

```text
id
patient_id
encounter_id
test_name
canonical_name
value
unit
reference_low
reference_high
abnormal_flag
test_time
source_image_id
ocr_confidence
normalization_confidence
```

---

# 14. 舌象

第一阶段：

**只实现舌象，不要求面象达到临床级。**

流程：

```text
上传照片
 ↓
图片质量检测
 ↓
舌体定位
 ↓
舌体分割
 ↓
ROI
 ↓
Vision Model
 ↓
Structured Tongue Features
```

输出：

```json
{
  "image_quality": {
    "score": 0.0,
    "passed": true,
    "issues": []
  },
  "tongue_features": {
    "tongue_color": "",
    "tongue_shape": "",
    "tongue_body": "",
    "coating_color": "",
    "coating_thickness": "",
    "coating_texture": "",
    "greasy": false,
    "teeth_marks": false,
    "cracks": false,
    "moisture": ""
  },
  "observations": [],
  "confidence": 0.0
}
```

特别要求：

不要允许视觉模型凭空生成没有检测到的细节。

要有：

```text
observed
not_observed
uncertain
```

三种状态。

---

# 15. 西医 AI 分析

调用：

```text
Main Clinical Brain
```

输入：

```text
患者基础信息
+
历史病历
+
本次主诉
+
结构化病史
+
检验
+
当前用药
+
过敏
+
舌象特征
+
必要图片
```

输出：

```json
{
  "summary": "",
  "problem_list": [],
  "clinical_findings": [],
  "differential_diagnoses": [
    {
      "diagnosis": "",
      "supporting_evidence": [],
      "contradicting_evidence": [],
      "additional_information_needed": [],
      "confidence_level": "low|medium|high"
    }
  ],
  "red_flags": [],
  "suggested_examinations": [],
  "clinical_reasoning": []
}
```

重要：

不要输出一个唯一“诊断答案”。

使用：

```text
候选诊断
+
支持证据
+
反证
+
仍需要的信息
```

---

# 16. 中医 AI 分析

调用：

```text
ShizhenGPT
+
TCM RAG
```

输入：

```text
症状
舌象
既往史
问诊
医生填写的四诊信息
```

输出：

```json
{
  "wang": {
    "tongue": [],
    "facial": [],
    "other": []
  },
  "wen": [],
  "wen_questionnaire": [],
  "qie": [],
  "syndrome_candidates": [
    {
      "name": "",
      "supporting_evidence": [],
      "contradicting_evidence": [],
      "confidence_level": "low|medium|high"
    }
  ],
  "treatment_principles": [],
  "formula_candidates": [],
  "herb_candidates": []
}
```

---

# 17. 中西医分析必须并行

不要：

```text
西医 → 中医 → 最终答案
```

要：

```text
                   Clinical Context
                         │
          ┌──────────────┴──────────────┐
          │                             │
          ▼                             ▼
   Western Reasoning              TCM Reasoning
          │                             │
          └──────────────┬──────────────┘
                         ▼
                  Synthesis Layer
```

Synthesis Layer 使用 Main Clinical Brain：

```text
Qwen3.8
```

但它不能覆盖原始西医/中医分析。

最终保留：

```text
Western Analysis
TCM Analysis
Vision Analysis
Synthesis
```

四块分别展示。

---

# 18. 知识库系统

必须支持以下知识源：

```text
西医：
- 医学教材
- 疾病知识
- 临床指南
- 医学 QA
- 专家经验

中医：
- 中医经典
- 中医教材
- 经方
- 方剂
- 中药
- 医案
- Shizhen corpus
- TCMChat

医院：
- 院内协定方
- 药品字典
- 科室规范
- 医生经验
- 院内路径
```

---

# 19. 知识库分层

必须支持：

```text
TIER_A_HOSPITAL
TIER_B_OFFICIAL
TIER_C_TEXTBOOK
TIER_D_SPECIALIST_MODEL
TIER_E_OPEN_CORPUS
```

不要把所有来源混成同一级。

---

# 20. RAG Pipeline

实现：

```text
User Clinical Context
        ↓
Query Rewrite
        ↓
Embedding
        ↓
Vector Search
        ↓
Keyword Search
        ↓
Fusion
        ↓
Reranker
        ↓
Top Evidence
        ↓
Main Model
```

最终返回：

```json
{
  "source_id": "",
  "title": "",
  "excerpt": "",
  "source_type": "",
  "trust_tier": "",
  "relevance_score": 0.0
}
```

医生页面允许点击：

```text
查看依据
```

看到：

```text
来源
章节
原文
更新时间
知识库版本
```

---

# 21. 最重要的一条：RAG 不得“控制模型思考”

产品定位：

> **模型自主分析 + 知识库提供证据**

而不是：

> 检索结果决定答案。

Prompt 设计要求：

```text
先独立分析
再检查证据
如证据支持则增强
如证据冲突则明确说明
如证据不足则不要假装确定
```

---

# 22. 药物与方剂模块

分三个区域：

```text
西药
中成药
中药饮片 / 膏方
```

AI 只生成：

```text
Prescription Draft
```

不是正式处方。

---

# 23. 西药建议

例如：

```json
{
  "medication": "",
  "reason": "",
  "dose": "",
  "frequency": "",
  "duration": "",
  "considerations": [],
  "evidence": []
}
```

同时检查：

```text
过敏
重复用药
禁忌
已知相互作用
患者年龄
现有药物
已有化验
肝肾相关信息
```

---

# 24. 中成药

同样：

```text
Medication Candidate
```

只能从：

```text
Hospital Drug Dictionary
```

以及开放知识中候选。

如果不在院内药品字典：

```text
标记：
“院内未配置”
```

不允许伪造 HIS 药品编码。

---

# 25. 中药饮片 / 经方 / 协定方

分成：

```text
Classic Formula
Hospital Formula
Custom Herb Draft
```

必须区分：

```text
经典来源
医院协定方
AI 自主组合
```

尤其 AI 自主组合必须显式标注：

```text
AI-generated candidate
```

不能伪装成：

```text
院内协定方
```

---

# 26. 医生工作台

这是整个产品最重要的界面。

页面建议：

```text
┌────────────────────────────────────────────────────┐
│ 患者：王某某   42岁   男                     就诊 # │
├──────────────┬─────────────────────────────────────┤
│              │                                     │
│ 患者信息      │       临床 AI 副驾                  │
│              │                                     │
│ 主诉          │ [西医分析] [中医辨证] [舌象]       │
│ 现病史        │                                     │
│ 既往史        │ 西医：                               │
│ 过敏          │ 候选诊断                            │
│ 用药          │ 支持证据                            │
│ 检验          │ 反证                                │
│ 舌象          │                                     │
│              │ 中医：                               │
│              │ 证型候选                            │
│              │ 治法                                │
│              │                                     │
│              │ 处方草稿                            │
│              │ [接受] [修改] [拒绝]                 │
├──────────────┴─────────────────────────────────────┤
│ 正式病历编辑器                                      │
│                                                     │
│                         [保存] [签名] [提交HIS]      │
└─────────────────────────────────────────────────────┘
```

---

# 27. AI 输出和正式病历必须视觉隔离

必须明确：

```text
AI 辅助建议
```

和：

```text
正式医疗文书
```

完全不同。

AI 内容绝对不能默认写进正式病历。

必须：

```text
AI Suggestion
 ↓
医生确认
 ↓
Insert to Draft
 ↓
医生编辑
 ↓
保存
```

---

# 28. AI 处方的 UI

必须显示：

```text
AI 处方草稿
```

医生可以：

```text
接受
修改
删除
新增
```

最终：

```text
医生确认
 ↓
电子签名
 ↓
提交 HIS
```

---

# 29. HIS Adapter

先做：

```text
MockHISAdapter
```

模拟 API：

```text
GET /mock-his/patients
GET /mock-his/patients/{id}
GET /mock-his/encounters/{id}
GET /mock-his/drugs
GET /mock-his/formulas

POST /mock-his/medical-records
POST /mock-his/prescriptions
POST /mock-his/billing
```

所有 HIS 写操作：

```text
必须带：
doctor_id
encounter_id
timestamp
request_id
signature_id
```

---

# 30. AI 故障降级

这是硬要求。

模拟：

```text
AI DOWN
```

然后：

```text
Doctor Workbench
 ↓
AI unavailable
 ↓
患者信息仍正常
 ↓
正式病历仍正常
 ↓
Mock HIS 仍正常
 ↓
处方仍可正常走原始流程
```

不要让任何 AI API 出错导致主业务报错。

---

# 31. 随访系统

实现：

```text
Followup Plan
```

支持：

```text
7 days
14 days
30 days
90 days
```

支持模板：

```text
糖尿病
高血压
呼吸系统
消化系统
中医慢病调养
自定义
```

---

# 32. 随访 AI

患者收到：

```text
最近情况怎么样？
咳嗽是否改善？
一天大约咳几次？
是否有胸痛？
是否发热？
是否按时服药？
是否出现药物不适？
```

AI 根据回答进行：

```text
症状抽取
变化趋势
依从性
不良反应
风险识别
```

---

# 33. 高危预警

实现：

```text
Patient
 ↓
Followup Response
 ↓
LLM structured extraction
 ↓
Rule Engine
 ↓
Risk Score
 ↓
Normal / Attention / High Risk
```

LLM 不得独自决定所有高危事件。

至少支持硬规则：

```text
胸痛
呼吸困难
晕厥
意识改变
严重出血
严重过敏
明显恶化
极端生命体征
严重低血糖
```

匹配到时：

```text
立即人工升级
```

---

# 34. 随访写回病历

必须支持：

```text
FollowupRecord
```

医生确认后：

```text
回写 Encounter / Medical Record
```

保存：

```text
患者回答
AI总结
风险等级
医生处理
时间
操作者
```

---

# 35. 数据库核心表

至少建立：

```text
users
roles
patients
encounters
medical_records
symptoms
lab_reports
lab_results
medications
allergies
tongue_images
tongue_observations

ai_runs
ai_outputs
ai_evidence
ai_feedback

knowledge_sources
knowledge_documents
knowledge_chunks

formula
formula_ingredients
hospital_formula

drugs
drug_rules

prescription_drafts
prescription_items
prescription_reviews

followup_plans
followup_tasks
followup_responses
followup_risk_events

audit_logs
system_events
```

---

# 36. AI Run 必须可追踪

`ai_runs` 至少有：

```text
id
patient_id
encounter_id
model_name
model_version
prompt_version
knowledge_version
input_hash
output_hash
status
latency_ms
created_at
```

`ai_outputs`：

```text
ai_run_id
output_type
structured_json
display_text
```

---

# 37. 医生反馈必须留存

例如：

```text
AI：
考虑 A

医生：
Reject
```

记录：

```text
ai_output_id
doctor_id
action
original_content
edited_content
reason
timestamp
```

以后这些数据可以形成：

```text
Preference Dataset
```

但第一阶段**不要自动拿来训练模型**。

---

# 38. 审计系统

任何关键行为：

```text
登录
查看患者
查看病历
上传文件
调用AI
查看AI证据
接受AI建议
修改AI建议
签名
提交HIS
修改处方
随访
高危预警
```

全部记录。

使用：

```text
append-only audit log
```

---

# 39. 权限

第一阶段至少：

```text
ADMIN
DOCTOR
NURSE
PHARMACIST
PATIENT
```

医生只能看到授权范围内的患者。

管理员：

```text
知识库管理
模型管理
系统配置
审计
```

患者：

```text
只能访问自己的数据
```

---

# 40. 知识库后台

必须做管理页面：

```text
知识库
├── 西医知识
├── 中医知识
├── 经典经方
├── 中药
├── 院内协定方
├── 药品
├── 专家经验
└── 指南
```

支持：

```text
上传 PDF
上传 Word
上传 Markdown
上传 TXT
上传 CSV
粘贴文本
```

自动：

```text
parse
 ↓
chunk
 ↓
metadata
 ↓
embedding
 ↓
index
```

---

# 41. 医院协定方后台

必须支持：

```text
新增
编辑
版本
启用
停用
审核人
生效时间
```

这部分不能由 AI 自动修改。

---

# 42. Drug Dictionary

支持：

```text
药品名称
通用名
规格
单位
剂型
HIS code
医保编码
禁忌
说明
```

允许 CSV 导入。

创建：

```text
scripts/import_drugs.py
```

因为未来医院只需要：

```bash
python scripts/import_drugs.py drugs.csv
```

就可以导入本院药品。

---

# 43. AI Orchestrator

这是整个系统核心。

设计成：

```text
ClinicalAnalysisWorkflow
```

流程：

```text
Load Encounter
      ↓
Build Clinical Context
      ↓
Parallel:
 ├── Western Analysis
 ├── TCM Analysis
 ├── Tongue Analysis
 └── Relevant Knowledge Retrieval
      ↓
Medication Safety Retrieval
      ↓
Prescription Candidate Generation
      ↓
Clinical Synthesis
      ↓
Structured Output
      ↓
Store AI Run
      ↓
Doctor Workbench
```

---

# 44. 并行调用

后端必须使用 async / task group / background jobs。

不要：

```python
await western()
await tcm()
await tongue()
```

串行慢调用。

优先：

```python
await asyncio.gather(
    western(),
    tcm(),
    tongue(),
    retrieve_knowledge()
)
```

---

# 45. Prompt 管理

不要把大 Prompt 写死在 Python。

建立：

```text
prompts/
├── western_analysis.md
├── tcm_analysis.md
├── tongue_analysis.md
├── prescription_draft.md
├── synthesis.md
├── followup.md
└── risk_triage.md
```

同时保存：

```text
prompt_version
```

数据库记录使用的版本。

---

# 46. Structured Output

所有核心 AI 模块必须使用 Pydantic Schema。

例如：

```text
WesternAnalysis
TCMAnalysis
TongueAnalysis
PrescriptionDraft
RiskAssessment
FollowupSummary
ClinicalSynthesis
```

禁止核心业务依赖：

```text
regex
```

从 Markdown 中猜结构。

---

# 47. 医学推理 Prompt 风格

要求模型：

```text
1. 先提取事实
2. 再识别问题
3. 再做候选分析
4. 明确支持证据
5. 明确反证
6. 指出信息缺口
7. 给出辅助建议
8. 不伪造检查
9. 不伪造知识库来源
10. 不把不确定推理包装成确定结论
```

但是：

**不要为了“安全”把模型变成只会拒答的机器人。**

它应该积极进行真正的临床辅助分析，只需明确：

```text
这是 AI 辅助建议
最终由医生判断
```

---

# 48. 中医 Prompt

要求：

```text
先提取四诊信息
 ↓
识别证候候选
 ↓
说明证据
 ↓
指出矛盾信息
 ↓
给治法候选
 ↓
再检索经典/经方/院内协定方
 ↓
给出方药候选
```

禁止：

```text
看到一个症状
→ 直接确定唯一证型
```

---

# 49. 多模型融合

最终输出页面：

```text
西医分析
中医辨证
舌象分析
综合分析
```

如果模型之间冲突：

```text
必须显示：
“模型意见存在差异”
```

例如：

```text
西医模型：
A

中医模型：
B

综合模型：
A、B分别基于不同医学框架，当前信息不足以统一。
```

不要偷偷选一个。

---

# 50. Demo 页面必须真的调用模型

严禁：

```text
假 AI
```

不能把结果写死。

点击：

```text
开始 AI 分析
```

必须真正：

```text
Frontend
 ↓
FastAPI
 ↓
AI Orchestrator
 ↓
真实模型
 ↓
返回结果
 ↓
UI
```

如果模型不可用：

```text
清楚显示服务故障
```

不要 fallback 成假数据却伪装成 AI。

---

# 51. Demo Seed

创建：

```text
scripts/seed_demo.py
```

初始化：

```text
2~5 个假患者
3 个假医生
若干就诊
若干检验
若干处方
若干知识条目
2~3 个院内协定方
10~20 个药品
```

数据必须明确：

```text
DEMO ONLY
```

---

# 52. 最少准备几个 Demo 病例

至少：

```text
Case 1：
呼吸系统

Case 2：
消化系统

Case 3：
代谢/慢病

Case 4：
中医典型辨证

Case 5：
需要高危随访
```

这些病例主要用来验证 Workflow，不作为“医疗真值”。

---

# 53. 评测系统

增加：

```text
evaluation/
```

支持：

```text
case replay
```

给同一病例运行：

```text
Western
TCM
Vision
Synthesis
Prescription
```

保存：

```text
latency
tokens
errors
structured_validity
retrieval_quality
```

以后可以比较不同模型。

---

# 54. 不要在 MVP 阶段训练模型

明确：

```text
NO fine-tuning
NO LoRA
NO RLHF
NO preference optimization
```

除非我后续明确要求。

---

# 55. 未来的数据闭环

架构必须预留：

```text
AI Proposal
 ↓
Doctor Accept/Edit/Reject
 ↓
Final Signed Clinical Record
 ↓
Outcome
```

以后可以形成：

```text
Clinical Preference Dataset
```

但现在只记录，不训练。

---

# 56. 可观察性

实现至少：

```text
health endpoint
readiness endpoint
metrics
structured logs
```

API：

```text
/health
/ready
/metrics
```

模型层记录：

```text
request_id
model
latency
tokens
status
```

---

# 57. Docker

第一阶段尽量：

```text
docker compose up
```

启动：

```text
PostgreSQL
Redis
MinIO
API
Frontend
Knowledge Service
Mock HIS
```

模型服务如果不适合 Docker GPU，也可以：

```text
host process
```

但 API 必须统一。

---

# 58. README 必须做到任何工程师拿到服务器都能启动

README 必须包括：

```text
1. 环境要求
2. 如何检查 GPU
3. 如何发现模型
4. 如何配置模型路径
5. 如何启动 model servers
6. 如何启动 PostgreSQL
7. 如何启动 Redis
8. 如何启动 MinIO
9. 如何初始化数据库
10. 如何导入知识库
11. 如何导入 Demo
12. 如何启动前端
13. 如何访问
14. Demo 账号
15. 如何关闭 AI
16. 如何模拟 AI 故障
17. 如何模拟 HIS
```

---

# 59. 创建统一配置

建立：

```text
.env.example
```

至少：

```env
APP_ENV=development

POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=ai_clinical
POSTGRES_USER=ai_clinical
POSTGRES_PASSWORD=change-me

REDIS_URL=redis://localhost:6379

MINIO_ENDPOINT=http://localhost:9000
MINIO_ACCESS_KEY=change-me
MINIO_SECRET_KEY=change-me

MODEL_QWEN_PATH=
MODEL_SHIZHEN_PATH=
MODEL_ASR_PATH=
MODEL_EMBEDDING_PATH=
MODEL_RERANKER_PATH=

QWEN_BASE_URL=
SHIZHEN_BASE_URL=
ASR_BASE_URL=
EMBEDDING_BASE_URL=
RERANKER_BASE_URL=

ENABLE_SECOND_OPINION=false
ENABLE_REAL_HIS=false
```

---

# 60. 建立模型注册表

文件：

```text
infra/models.yaml
```

结构：

```yaml
models:
  clinical_main:
    enabled: true
    provider: openai_compatible
    base_url: ...
    model: ...

  tcm:
    enabled: true
    provider: openai_compatible
    base_url: ...
    model: ...

  asr:
    enabled: true

  embedding:
    enabled: true

  reranker:
    enabled: true

  second_opinion:
    enabled: false
```

---

# 61. 错误处理

任何 AI 调用：

```text
timeout
retry
circuit breaker
fallback state
```

但不要无限重试。

状态：

```text
PENDING
RUNNING
SUCCEEDED
PARTIAL
FAILED
TIMEOUT
```

如果一个并行模型失败：

```text
Western SUCCEEDED
TCM SUCCEEDED
Tongue FAILED
```

最终允许：

```text
PARTIAL RESULT
```

不能整单失败。

---

# 62. AI 状态 UI

例如：

```text
西医分析       ✓
中医辨证       ✓
舌象分析       ⚠ 暂不可用
知识检索       ✓
综合分析       ✓
```

---

# 63. AI 输出必须有来源链

医生点击：

```text
为什么这样判断？
```

显示：

```text
Model:
Qwen3.8-27B

Prompt:
western_analysis v1.0

Knowledge:
source #1023
source #9321

Generated:
2026-10-07 ...

Clinical Input:
encounter #...
```

---

# 64. 数据安全

即使这是小型医院，也必须做到：

```text
RBAC
审计
权限
TLS
密码哈希
session expiration
```

但不需要过早做复杂企业 IAM。

可以先：

```text
JWT
Role Based Access Control
```

以后再接：

```text
OIDC / Keycloak / Hospital SSO
```

---

# 65. 真实 HIS 接入的边界

当前：

```text
REAL_HIS = false
```

所有 HIS API 调用经过：

```text
HISAdapter
```

不要在任何其他 service 中写：

```python
requests.post("http://his...")
```

必须：

```python
his_adapter.create_prescription(...)
```

---

# 66. 所有正式医疗状态必须服务端控制

例如：

```text
PrescriptionDraft
```

状态：

```text
AI_GENERATED
DOCTOR_EDITING
DOCTOR_CONFIRMED
SIGNED
SUBMITTED_TO_HIS
HIS_ACCEPTED
CANCELLED
```

前端不能直接修改状态。

---

# 67. 电子签名第一版

MVP 可以：

```text
doctor login
+
password confirmation
+
sign action
+
audit log
```

抽象：

```text
SignatureService
```

未来再接医院真实 CA / UKey / 电子签名服务。

---

# 68. UI 风格

不要做：

```text
ChatGPT clone
```

要做：

```text
医护工作站
```

视觉风格：

```text
白色
浅灰
蓝/青医疗色系
高信息密度
清晰分区
状态明显
表格清晰
卡片适量
```

AI 的颜色只用于：

```text
辅助信息
```

正式病历使用：

```text
普通医疗文书样式
```

---

# 69. 医生首页

默认：

```text
今日门诊
待接诊
正在接诊
待审核 AI
待签名
待随访
高危预警
```

顶部：

```text
AI 服务状态：
● 正常
```

模拟 AI 故障时：

```text
● AI 辅助暂不可用
HIS 主流程不受影响
```

---

# 70. 关键页面

至少完成：

```text
/login

/doctor/dashboard

/doctor/patients

/doctor/encounter/:id

/doctor/encounter/:id/ai

/doctor/prescription/:id

/doctor/followups

/doctor/alerts

/patient/home

/patient/encounter/:id

/patient/upload-report

/patient/tongue

/patient/followup

/admin/knowledge

/admin/formulas

/admin/drugs

/admin/users

/admin/audit
```

---

# 71. API 至少实现

```text
POST /api/auth/login

GET /api/patients
GET /api/patients/{id}

GET /api/encounters/{id}

POST /api/encounters/{id}/voice
POST /api/encounters/{id}/ocr
POST /api/encounters/{id}/tongue

POST /api/encounters/{id}/ai/analyze
GET /api/ai-runs/{id}

POST /api/ai-outputs/{id}/feedback

POST /api/prescriptions/drafts
PATCH /api/prescriptions/drafts/{id}
POST /api/prescriptions/drafts/{id}/sign
POST /api/prescriptions/{id}/submit-his

GET /api/knowledge/search

POST /api/knowledge/documents

GET /api/formulas
POST /api/formulas
PATCH /api/formulas/{id}

GET /api/drugs

POST /api/followups
GET /api/followups
POST /api/followups/{id}/response

GET /api/alerts
```

---

# 72. OpenAI-compatible 模型 Gateway

统一：

```text
/model-gateway
```

内部接口：

```text
POST /chat
POST /vision
POST /embeddings
POST /rerank
POST /asr
```

模型具体地址由配置控制。

业务层不允许知道：

```text vLLM
SGLang
Transformers
llama.cpp
```

---

# 73. Knowledge Ingestion

实现 pipeline：

```text
Document
 ↓
Parser
 ↓
Cleaner
 ↓
Metadata Extractor
 ↓
Chunker
 ↓
Embedding
 ↓
Vector DB
```

Chunk 建议：

```text
500~1200 Chinese characters
```

但允许按标题/章节自动切分。

不要单纯按固定字符硬切。

---

# 74. 医学知识 Chunk metadata

至少：

```json
{
  "source_type": "textbook",
  "domain": "tcm",
  "subdomain": "formula",
  "title": "",
  "author": "",
  "book": "",
  "chapter": "",
  "year": null,
  "trust_tier": "C",
  "version": ""
}
```

---

# 75. 知识库第一阶段数据

如果机器上已经存在这些数据：

```text
TCM Shizhen corpus
Shizhen instruction
TCMChat
Huatuo data
```

自动扫描。

不要重新下载。

如果没找到：

**不要主动重新下载。**

建立：

```text
docs/resource-manifest.md
```

列出：

```text
资源名
路径
是否存在
大小
license
用途
```

---

# 76. 不允许做的事情

禁止：

```text
1. 下载大量重复模型
2. 删除已有模型
3. 修改系统 CUDA
4. 升级驱动
5. 覆盖生产 Python
6. 修改真实 HIS
7. 连接真实患者数据
8. 修改真实医院数据库
9. 自动上传医疗数据到外部 API
10. 将患者数据发送给公网模型
```

除非我明确授权。

---

# 77. 网络访问

系统必须默认：

```text
NO_EXTERNAL_LLM = true
```

即：

```text
患者数据
 ↓
只进入内网模型
```

即使公网可访问，也不能默认把医疗数据发送到外部 API。

---

# 78. 开发环境

全部服务先在：

```text
development
```

运行。

创建：

```text
.env
```

来源：

```text
.env.example
```

---

# 79. 测试

至少写：

```text
backend unit tests
API tests
AI schema validation tests
RAG tests
HIS adapter tests
frontend smoke tests
```

关键 E2E：

```text
Patient create
 ↓
Encounter create
 ↓
Upload transcript
 ↓
OCR
 ↓
Tongue
 ↓
AI Analysis
 ↓
Doctor Review
 ↓
Prescription Draft
 ↓
Sign
 ↓
Mock HIS
 ↓
Followup Plan
 ↓
Followup Response
 ↓
Risk Alert
```

必须真正跑通。

---

# 80. 模拟 AI 故障测试

实现：

```text
ENABLE_AI_FAILURE_SIMULATION=true
```

能够让：

```text
Qwen
Shizhen
OCR
ASR
```

任意一个模拟 unavailable。

然后验证：

```text
HIS workflow continues.
```

---

# 81. 模型启动完成后的测试

每个模型都做一个 smoke test。

例如：

```text
Qwen:
“请将以下病情整理成结构化病历……”

Shizhen:
“患者咳嗽、痰少、舌淡红苔白……请分析证候候选。”

ASR:
一段 Demo audio

Embedding:
一条 query

Reranker:
query + documents

OCR:
一张 demo report
```

把结果保存：

```text
artifacts/model-smoke-tests/
```

---

# 82. 性能目标

MVP 不追求极端并发。

目标：

```text
文本临床分析：
< 15 sec

中医分析：
< 15 sec

OCR：
< 10 sec

ASR：
接近实时

RAG：
< 2 sec

页面首次打开：
< 3 sec
```

如果性能达不到：

优先：

```text
并行化
量化
缓存
batch
```

不要直接换模型。

---

# 83. Prompt 与模型不要过度耦合

不要写：

```text
“Qwen 你必须……”
```

Prompt 写成：

```text
System:
You are a clinical AI assistant...
```

模型名通过 configuration 注入。

---

# 84. 结果缓存

允许：

```text
同一 encounter
+
同一 clinical input hash
+
同一 model
+
同一 prompt version
+
同一 knowledge version
```

复用结果。

这样开发阶段不用反复烧 GPU。

---

# 85. AI 分析必须支持重新运行

医生可以：

```text
重新分析
```

但不能覆盖旧结果。

而是：

```text
AI Run #1
AI Run #2
AI Run #3
```

这样可以审计。

---

# 86. 版本体系

必须支持：

```text
Model Version
Prompt Version
Knowledge Version
Rule Version
```

每个 AI 输出记录四者。

---

# 87. 医疗规则不是第一阶段的“大规则引擎”

第一阶段只做最必要的：

```text
allergy
duplicate medication
missing required fields
obvious contraindication
obvious drug interaction if dictionary supports
high-risk followup keywords
```

不要一开始构建几千条规则。

我们的目标是：

> 模型自主推理 + 最小必要硬约束。

---

# 88. 代码质量要求

必须：

```text
Type hints
Pydantic
SQLAlchemy
async where appropriate
structured logging
error handling
tests
```

禁止：

```text
TODO 敷衍
大段伪代码
mock model masquerading as real
hardcoded patient results
hardcoded AI output
```

---

# 89. 任何不确定的地方

不要暂停询问我。

采用：

```text
best engineering judgment
```

并记录到：

```text
docs/assumptions.md
```

例如：

```text
Assumption:
真实 HIS API 未提供，因此目前使用 MockHISAdapter。
```

继续开发。

---

# 90. 开发顺序

严格按照这个优先级。

## Phase 0

环境检查：

```text
GPU
CUDA
Python
Docker
模型
磁盘
```

输出：

```text
ENVIRONMENT REPORT
```

---

## Phase 1

项目骨架：

```text
repo
frontend
backend
database
docker compose
```

---

## Phase 2

模型 Gateway：

```text
Qwen
Shizhen
ASR
Embedding
Reranker
OCR
```

---

## Phase 3

Clinical Context：

```text
Patient
Encounter
Medical Record
Lab
Medication
Allergy
Tongue
```

---

## Phase 4

AI Workflow：

```text
Western
TCM
Tongue
Synthesis
```

---

## Phase 5

Knowledge Base：

```text
upload
parse
chunk
embed
rerank
citation
```

---

## Phase 6

Prescription Draft：

```text
western medicine
patent medicine
herbs
formula
```

---

## Phase 7

Doctor Workbench：

```text
review
edit
accept
reject
sign
```

---

## Phase 8

Mock HIS：

```text
submit
billing
pharmacy
```

---

## Phase 9

Patient Portal：

```text
voice
OCR
tongue
history
```

---

## Phase 10

Followup：

```text
plan
task
conversation
risk
alert
record
```

---

## Phase 11

Audit：

```text
AI run
doctor actions
HIS submission
```

---

## Phase 12

E2E：

```text
Patient
→
Doctor
→
AI
→
Prescription
→
HIS
→
Followup
```

---

# 91. 每个 Phase 完成后必须实际运行

不要写完所有代码才启动。

每个阶段完成：

```text
install
start
healthcheck
test
fix
```

然后继续。

---

# 92. 启动命令最终必须做到

开发环境：

```bash
make dev
```

或者：

```bash
./scripts/start-dev.sh
```

能够启动：

```text
Frontend
API
Postgres
Redis
MinIO
Mock HIS
Knowledge
AI Gateway
```

如果模型 server 是宿主机：

```text
start-dev.sh
```

至少检查它们是否已运行。

---

# 93. 最终访问地址

默认：

```text
Doctor:
http://localhost:3000/doctor

Patient:
http://localhost:3000/patient

Admin:
http://localhost:3000/admin

API:
http://localhost:8000

Docs:
http://localhost:8000/docs
```

端口冲突时自动修改并记录。

---

# 94. Demo 登录

创建：

```text
doctor@example.local
nurse@example.local
admin@example.local
patient@example.local
```

所有密码只用于 Demo。

README 明确：

```text
DEMO ONLY
```

---

# 95. 最终必须给我的输出

当 MVP 完成后，不要只告诉我：

> “代码完成了。”

必须输出一份：

```text
docs/final-status.md
```

包括：

1. 环境
2. GPU 分配
3. 模型路径
4. 模型运行时
5. API
6. 数据库
7. 知识库
8. 已实现功能
9. 未实现功能
10. 已知问题
11. 启动方法
12. 测试结果
13. AI smoke test
14. E2E test
15. AI failure test
16. HIS mock test
17. 后续真实 HIS 接入点

---

# 96. 第一件事情现在立刻做

不要先创建大量 UI。

首先：

```text
1. 探测服务器
2. 找到所有已下载模型
3. 检查模型文件
4. 检查每个模型的 license / architecture metadata（本地文件优先）
5. 检查运行时
6. 检查 GPU
7. 输出 RESOURCE REPORT
8. 给每个模型建立统一配置
9. 做最小 smoke test
10. 然后开始搭系统
```

执行后创建：

```text
docs/environment-report.md
docs/resource-manifest.md
infra/models.yaml
```

---

# 97. RESOURCE REPORT 格式

必须生成：

```markdown
# Resource Report

## Hardware

## GPU

## CUDA

## Python

## Docker

## Models

| Model | Path | Size | Runtime | GPU | Status |
|---|---|---:|---|---|---|

## Datasets

| Dataset | Path | Size | License | Status |
|---|---|---:|---|---|

## OCR

## Embedding

## Reranker

## Storage

## Problems

## Recommended Allocation
```

---

# 98. 不要下载新资源

非常重要：

> 当前模型已经下载完成。

除非发现：

```text
模型损坏
模型不完整
模型无法加载
缺失关键文件
```

否则：

**不要主动重新下载模型。**

如果需要某个小依赖：

```text
可以安装
```

但先检查本地环境。

---

# 99. 最终产品目标

完成后，我希望打开浏览器看到：

```text
              AI 中西医临床副驾
────────────────────────────────────────

今日门诊      待审核AI      待签名      高危随访

患者：王某某
男 / 42岁

主诉
反复咳嗽3周

────────────────────────────────────────

             AI 临床分析

[西医] [中医辨证] [舌象] [综合]

西医
────────────────
问题列表
鉴别诊断
证据
反证
建议补充信息

中医
────────────────
四诊信息
证候候选
辨证依据
治法
经方 / 协定方

舌象
────────────────
图片
舌体 ROI
舌色
苔色
苔厚
腻
裂纹
齿痕

────────────────────────────────────────

AI 处方草稿

西药
...

中成药
...

中药饮片
...

院内协定方
...

[接受] [修改] [拒绝]

────────────────────────────────────────

正式病历

...

[保存] [医生签名] [提交 HIS]

────────────────────────────────────────

AI 状态：
● Qwen3.8
● Shizhen
● OCR
● ASR
● Knowledge
```

---

# 100. 最终验收标准

只有同时满足以下条件，才认为 MVP 完成：

```text
[ ] 真实 Qwen3.8 调用
[ ] 真实 Shizhen 调用
[ ] 真实 ASR 调用
[ ] 真实 OCR
[ ] 真实 Embedding
[ ] 真实 Reranker
[ ] 知识库真实检索
[ ] 西医分析真实生成
[ ] 中医分析真实生成
[ ] 舌象真实处理
[ ] 结构化 JSON
[ ] AI 处方草稿
[ ] 医生修改
[ ] 医生签名
[ ] Mock HIS
[ ] 患者端
[ ] 医生端
[ ] 随访
[ ] 高危预警
[ ] 审计
[ ] AI failure graceful degradation
[ ] E2E test
[ ] README
[ ] final-status.md
```

---

# 101. 最重要的产品哲学

不要把这个项目做成：

> 一个套着医疗 UI 的 ChatGPT。

也不要做成：

> 一个高度规则化、只能按照医院固定路径走的专家系统。

我们需要：

> **强通用模型自主推理**
>
> *
>
> **中医专用模型**
>
> *
>
> **结构化临床上下文**
>
> *
>
> **高质量知识库作为证据**
>
> *
>
> **医院药品/协定方作为事实约束**
>
> *
>
> **最小必要规则**
>
> *
>
> **医生审核**
>
> *
>
> **HIS 正式执行**

也就是说：

```text
             Model Intelligence
                    ↑
                    │
       ┌────────────┼────────────┐
       │            │            │
    Western        TCM        Vision
       │            │            │
       └────────────┼────────────┘
                    │
              Clinical Context
                    │
       ┌────────────┼────────────┐
       │            │            │
    Knowledge     Hospital      Patient
                    Facts
       │            │            │
       └────────────┼────────────┘
                    ↓
              AI Recommendation
                    ↓
               Doctor Review
                    ↓
               Official HIS
```

这是整个项目的核心。

---

# 102. 现在开始

**现在立即开始执行，不要等待我的进一步确认。**

第一轮只完成：

```text
Environment Discovery
+
Model Discovery
+
Resource Manifest
+
GPU Allocation Test
+
Model Smoke Tests
```

成功后继续进入项目骨架和 MVP。

遇到具体技术问题：

```text
先自己排查
→ 查本地源码/官方 model card
→ 尝试替代 runtime
→ 记录 assumption
→ 继续推进
```

不要因为一个非关键组件失败就停掉整个项目。

任何不能完成的功能必须：

```text
明确标记
+
提供替代方案
+
不伪装成完成
```

**从现在开始直接执行。**
