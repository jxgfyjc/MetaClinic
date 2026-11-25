# UI 重构计划 — 以 his-functional-blueprint.md 为模板

> 生成日期：2026-10-10 ｜ 依据：instruct/updatev1.md §1 ｜ 模板：instruct/his-functional-blueprint.md

## 1. 现有项目与蓝图对比

### 1.1 参考产品（甘草云管家）信息架构

蓝图描述了一个多租户 SaaS 诊所管理平台，标准版含 11 大导航组：
柜台工作台、医师工作站、药房管理、日常医务、会员管理、财务管理、基础资料、
微信公众号、诊所管理、数据报表、医保管理 + GSP + 系统相关。

核心工作流：预约→挂号→接诊→开方→收银→配药→发药；AI 语音病历；随访。

### 1.2 本院实际需求取舍

本院为小型医院，侧重中西医结合门诊 + AI 临床副驾。取舍如下：

| 蓝图模块 | 本院取舍 | 说明 |
|---|---|---|
| 柜台工作台 | ✅ 预约挂号接诊 | 保留预约/挂号/接诊，去掉收银（非核心） |
| 医师工作站 | ✅ 核心模块 | 临床诊疗室 = 现有 /encounters/[eid] AI 副驾 |
| 药房管理 | ✅ 药品管理+药房 | 西药/中成药/饮片/协定方 + 药房发药 |
| 日常医务 | ◐ 部分 | 保留排班/病历，去掉短信营销/发票 |
| 财务管理 | ❌ 暂不实现 | 标记为未实现，不开发复杂后端 |
| 基础资料 | ◐ 药品基础资料 | 药品/诊疗项目保留，器械/供应商标记未实现 |
| 诊所管理 | ✅ 员工权限审计 | 员工/权限/审计保留 |
| 数据报表 | ◐ 基础报表 | 门诊日志/统计标记为 Demo |
| 医保管理 | ❌ 暂不实现 | 标记为未实现 |
| 推拿针灸理疗康复 | ✅ 新增 | 本院特色，蓝图中诊疗项目的延伸 |

### 1.3 现有前端路由（重构前）

| 路由 | 状态 | 说明 |
|---|---|---|
| / | ✅ 工作台首页 | 有统计卡片，需升级为今日待办 |
| /patients | ✅ 患者列表 | 有表格，需加档案详情/时间线 |
| /patient | ✅ 患者随访端 | 随访回答提交，需并入随访模块 |
| /encounters | ✅ 就诊列表 | DB-backed，需升级为今日接诊 |
| /encounters/[eid] | ✅ 临床诊疗室 | 核心AI副驾，保留并融入框架 |
| /knowledge | ✅ 知识库检索 | RAG 检索，保留 |
| /admin | ✅ 管理端 | 药品/协定方字典，需拆分到药品管理 |

### 1.4 现有 API（可复用）

- 医生: GET /api/doctors, GET/PUT /api/doctors/{did}/profile
- 理疗: GET /api/therapy-programs, /api/therapy-programs/categories
- 就诊: GET /api/encounters, /api/encounters/{eid}, POST /api/encounters/{eid}/ai/analyze
- AI: GET /api/ai-runs/{rid}, POST /api/ai-outputs/{oid}/feedback
- 知识: GET /api/knowledge/search, POST /api/knowledge/documents
- 药品: GET /api/drugs, GET /api/formulas, POST /api/formulas
- MockHIS: /mock-his/patients, /encounters, /drugs, /formulas, /medical-records, /prescriptions
- 随访: POST /api/followups, GET /api/followups, POST /api/followups/{fid}/response, GET /api/alerts
- 处方: POST /api/prescriptions/drafts, PATCH, /confirm, /sign, /submit-his

## 2. 目标页面架构（侧边导航）

```
AI 临床副驾 · 门诊管理系统
├── 工作台 (/)                    — 今日待办 + 统计概览 [已有,升级]
├── 患者管理
│   ├── 患者列表 (/patients)     — 表格 [已有]
│   └── 患者档案 (/patients/[pid])— 档案+时间线 [新建]
├── 预约挂号
│   ├── 预约管理 (/appointments) — [新建, Demo 数据]
│   └── 挂号接诊 (/registration) — [新建, Demo 数据]
├── 医师工作站
│   ├── 今日接诊 (/encounters)   — [已有]
│   └── 临床诊疗室 (/encounters/[eid]) — AI 副驾 [已有, 核心]
├── 病历管理
│   ├── 病历列表 (/medical-records) — [新建, 接 API]
│   └── 处方模板 (/templates)    — [新建, Demo]
├── 药品管理
│   ├── 西药/成药 (/pharmacy/drugs) — [新建, 接 /api/drugs]
│   ├── 中药饮片 (/pharmacy/herbs) — [新建, Demo]
│   ├── 院内协定方 (/pharmacy/formulas) — [新建, 接 /api/formulas]
│   └── 药房发药 (/pharmacy/dispensing) — [新建, Demo]
├── 理疗康复
│   └── 治疗项目 (/therapy)      — [新建, 接 /api/therapy-programs]
├── 个性化配置
│   ├── 医师风格 (/doctors)      — [新建, 接 /api/doctors + profile]
│   └── 经验库 (/doctors/[did])  — [新建]
├── 随访管理
│   ├── 随访计划 (/followups)    — [新建, 接 /api/followups]
│   ├── 风险预警 (/alerts)       — [新建, 接 /api/alerts]
│   └── 患者随访端 (/patient)    — [已有, 保留]
├── 系统管理
│   ├── 知识库 (/knowledge)     — [已有]
│   ├── 员工权限 (/admin/staff) — [新建, Demo]
│   ├── 审计日志 (/admin/audit)  — [新建, Demo]
│   └── 基础报表 (/admin/reports) — [新建, Demo]
```

## 3. 开发步骤

1. 侧边导航组件 + 布局重构（layout.tsx）
2. 工作台首页升级（今日待办）
3. 患者档案+时间线
4. 预约挂号（Demo 标注）
5. 病历管理 + 处方模板
6. 药品管理（接 API）
7. 理疗康复项目
8. 医师个性化配置页（含风格自定义 UI）
9. 随访管理 + 风险预警
10. 系统管理（员工/审计/报表）
11. 统一视觉（面包屑、表格、详情页样式）
12. 回归测试 + 输出 docs/ui-reconstruction-status.md

## 4. 约束

- 保留现有 AI 核心（orchestrator/model-gateway/RAG/SQLite）
- 不重写后端，不破坏黄金路径
- 不下载新模型，不碰 8000/8188
- 未接通页面用显式 Demo 标注，不伪装完成
- 先布局/路由/视觉，再逐步接通 API
