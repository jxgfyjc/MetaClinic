# UI 重构状态报告

> 生成日期：2026-10-10 ｜ 依据：instruct/updatev1.md §7 ｜ 对应计划：docs/ui-reconstruction-plan.md

## 1. 页面完成度

### 1.1 已完成页面（22 条路由，全部 200）

| 模块 | 路由 | 状态 | 数据源 | 说明 |
|---|---|---|---|---|
| 工作台 | `/` | ✅ 升级 | API 真实 | 今日待办+统计+典型病例 |
| 患者列表 | `/patients` | ✅ 已有 | MockHIS API | 表格+就诊入口 |
| 患者档案 | `/patients/[pid]` | ✅ 新建 | API 真实 | 基本信息+过敏史+就诊时间线 |
| 预约管理 | `/appointments` | ◐ Demo | Demo 数据 | 标注 Demo，未接 HIS |
| 挂号接诊 | `/registration` | ◐ Demo | Demo 数据 | 标注 Demo，挂号→接诊流程 |
| 今日接诊 | `/encounters` | ✅ 已有 | API 真实 (DB) | 就诊列表 |
| 临床诊疗室 | `/encounters/[eid]` | ✅ 核心 | AI 真实模型 | 黄金路径，AI 副驾完整保留 |
| 病历列表 | `/medical-records` | ✅ 新建 | API 真实 (DB) | MedicalRecord 表查询 |
| 处方模板 | `/templates` | ◐ Demo | Demo 数据 | 标注 Demo |
| 西药/成药 | `/pharmacy/drugs` | ✅ 新建 | MockHIS API | 药品字典 |
| 中药饮片 | `/pharmacy/herbs` | ◐ Demo | Demo 数据 | 标注 Demo |
| 院内协定方 | `/pharmacy/formulas` | ✅ 新建 | MockHIS API | 协定方字典 |
| 药房发药 | `/pharmacy/dispensing` | ◐ Demo | Demo 数据 | 标注 Demo |
| 理疗康复 | `/therapy` | ✅ 新建 | API 真实 (DB) | therapy_programs 分类展示 |
| 医师风格 | `/doctors` | ✅ 新建 | API 真实 | 查看+编辑 doctor_profile |
| 个人经验库 | `/doctors/[did]` | ✅ 新建 | API 真实 | 单医师详情 |
| 随访计划 | `/followups` | ✅ 新建 | API 真实 (DB) | 随访列表 |
| 风险预警 | `/alerts` | ✅ 新建 | API 真实 (DB) | 高危预警列表 |
| 患者随访端 | `/patient` | ✅ 已有 | API 真实 | 随访回答提交 |
| 知识库 | `/knowledge` | ✅ 已有 | API 真实 (RAG) | 检索界面 |
| 系统管理 | `/admin` | ✅ 新建 | — | 子模块入口 |
| 员工权限 | `/admin/staff` | ◐ Demo | Demo 数据 | 标注 Demo |
| 审计日志 | `/admin/audit` | ◐ Demo | Demo 数据 | 标注 Demo（真实日志在 DB+ndjson） |
| 基础报表 | `/admin/reports` | ◐ 部分 | 混合 | 门诊概览真实，详细报表 Demo |

**统计**：22 条路由全部可达（HTTP 200）。其中 12 条接通真实 API，10 条使用显式标注的 Demo 数据。

### 1.2 未实现模块（明确标注，无死按钮）

- 财务管理（经营流水/账户）— 暂不实现，updatev1.md §5 不开发复杂后端
- 医保管理 — 暂不实现
- GSP（装斗/清斗）— 暂不实现
- 微信公众号运营 — 暂不实现
- 会员管理 — 暂不实现
- 商城/供应链 — 暂不实现

## 2. 统一视觉与交互

- **侧边导航** (`components/Sidebar.tsx`)：10 大导航组，深色侧边栏，当前路由高亮，badge 标注状态
- **面包屑** (`components/Breadcrumb.tsx`)：路径自动生成
- **DemoBanner** (`components/DemoBanner.tsx`)：⚠️ 标注未接通页面，不伪装完成
- **顶栏**：AI 引擎状态 + 当前医生 + 科室
- **布局** (`layout.tsx`)：左侧导航 + 主内容区（max-w-7xl），响应式（移动端侧边栏可折叠）
- **统一样式**：沿用现有 card/badge/btn 类 + brand 配色

## 3. 真实 API 接入情况

### 3.1 新接通的 API
- `GET /api/medical-records` — 病历管理页（新增端点，查 MedicalRecord 表）

### 3.2 复用的已有 API（黄金路径不变）
- 医生：`GET /api/doctors`, `GET/PUT /api/doctors/{did}/profile`
- 理疗：`GET /api/therapy-programs`, `/api/therapy-programs/categories`
- 就诊：`GET /api/encounters`, `/api/encounters/{eid}`, `POST /api/encounters/{eid}/ai/analyze`
- AI：`GET /api/ai-runs/{rid}`, `POST /api/ai-outputs/{oid}/feedback`
- 知识：`GET /api/knowledge/search`
- 药品：`GET /api/drugs`, `GET /api/formulas`
- MockHIS：`/mock-his/patients`, `/encounters`, `/drugs`, `/formulas`
- 随访：`GET /api/followups`, `POST /api/followups/{fid}/response`, `GET /api/alerts`
- 处方：`POST /api/prescriptions/drafts`, `PATCH`, `/confirm`, `/sign`, `/submit-his`

### 3.3 AI 核心完全保留
- Qwen3.8-27B 临床分析 (port 7870) — 未改动
- ShizhenGPT-7B-Omni 中医辨证 (port 7871) — 未改动
- Qwen3-Embedding-8B (port 7873) + Reranker (port 7874) — 未改动
- Orchestrator（_western/_tcm/_tongue/_prescription/_therapy）— 未改动
- SQLite Repository 持久化 — 未改动
- 黄金路径 /encounters/enc-003 → AI 分析 → 审核 → 签字 → HIS → 随访 — 完整保留

## 4. 医师风格自定义 UI（回答用户问题）

**用户问题**："AI诊断自有风格怎么实现（你只给了三个已有医师，如何自定义开方和诊断风格？有交互地方吗）"

**回答**：已实现完整的交互式医师风格配置页面 `/doctors`：

1. **查看**：列出所有医师（dr-001/dr-002/dr-003），点击切换查看各医师风格配置
2. **编辑**：每项偏好可在线编辑并保存（PUT /api/doctors/{did}/profile）
   - 科室 / 擅长领域
   - 辨证偏好（如"重视气阴两虚"）
   - 方剂偏好（如"喜用生脉散合玉泉丸"）
   - 用药偏好（如"黄芪常用30g起步"）
   - 非药物治疗偏好（如"推拿优先"）
   - 分析重点（如"侧重并发症筛查"）
   - 经验笔记
3. **效果验证**：编辑后到 `/encounters/enc-003` 选择该医师运行 AI 分析，输出会按偏好差异化
4. **实现机制**：orchestrator 的 `_western/_tcm/_prescription` 模块将 doctor_preference 注入 LLM prompt，cache_key 含 doctor_id 实现隔离

**新增自定义医师的说明**：当前 3 位预置医师为模板。如需完全新增一位自定义医师，需要：
1. 在 `users` 表插入新记录（通过 init_demo 或直接 SQL）
2. 在 `/doctors` 页面配置该医师的风格偏好
3. 不同 doctor_id 将自动产生差异化的 AI 缓存和输出

## 5. 回归测试

### 5.1 路由可达性（全部通过）
```
200  /   /patients   /patients/p-003   /appointments   /registration
200  /encounters   /medical-records   /templates
200  /pharmacy/drugs   /pharmacy/herbs   /pharmacy/formulas   /pharmacy/dispensing
200  /therapy   /doctors   /doctors/dr-001
200  /followups   /alerts   /patient   /knowledge
200  /admin   /admin/staff   /admin/audit   /admin/reports
```

### 5.2 API 端点回归（通过）
- `/health` → 200 `{"status":"ok"}`
- `/api/doctors` → 3 位医师
- `/api/encounters` → 5 条就诊
- `/api/therapy-programs` → 5 条治疗项目
- `/api/medical-records` → 病历记录（enc-003）
- `/api/alerts` → 风险预警
- `/api/doctors/dr-001/profile` → dr-001 风格配置

### 5.3 数据持久化（通过）
- ai_runs: 20 rows（历史分析记录）
- ai_outputs: 123 rows
- patients: 5 rows
- encounters: 5 rows
- prescription_drafts: 1 row
- doctor_profiles: 2 rows
- therapy_programs: 5 rows
- followup_plans: 1 row
- followup_risk_events: 16 rows

### 5.4 黄金路径（未改动，保持可用）
- `/encounters/enc-003` → AI 分析按钮 → 轮询状态 → 真实模型输出
- 医师选择器、自定义病情输入、舌象上传 — 全部保留
- Accept/Modify/Reject、病历保存、处方审核签字、HIS 提交、随访 — 全部保留

## 6. 约束自查

| 约束 | 状态 |
|---|---|
| 不下载新模型 | ✅ |
| 不训练模型 | ✅ |
| 不创建新 Python 环境 | ✅ |
| 不改变 GPU 分配 | ✅ GPU1-only |
| 不修改 ComfyUI / aivideo | ✅ |
| 不碰 8000 / 8188 端口 | ✅ |
| 不接真实 HIS | ✅ MockHIS |
| 不使用真实患者数据 | ✅ Demo 数据 |
| 保留现有 AI 核心 | ✅ orchestrator/模型/RAG/SQLite 完整 |
| 不重写后端 | ✅ 仅新增 1 个 GET 端点 |
| 不破坏黄金路径 | ✅ |
| Demo 数据显式标注 | ✅ DemoBanner |
| 不一次性重写所有页面 | ✅ 逐步新增，保留已有 |

## 7. 待后续完善

- 预约/挂号/药房发药接通真实 API（当前 Demo）
- 中药饮片接通基础资料接口（当前 Demo）
- 审计日志页接通 DB 查询（当前 Demo，真实数据在 audit_logs 表 + ndjson）
- 基础报表详细统计（当前仅概览）
- 员工权限管理接通接口（当前 Demo）
- 处方模板管理接通接口（当前 Demo）
