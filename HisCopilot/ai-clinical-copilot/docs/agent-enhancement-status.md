# Agent Enhancement Status — Orchestrator Audit & LangGraph Evaluation

> updatev1_2.md §7：审计 Orchestrator 架构，评估 LangGraph 是否值得迁移。
> 更新日期：2026-10-10。

## 一、当前 Orchestrator 架构分析

### 架构模式：静态并行流水线

当前 `services/ai_orchestrator/orchestrator.py` 采用**静态流水线**模式：

```
run(ctx) →
  _western(ctx)      ─┐
  _tcm(ctx)           ├─ 并行/顺序执行
  _tongue(ctx)        │
  _knowledge(ctx)    ─┘
  _synthesis(ctx)     ← 依赖 western+tcm+tongue
  _prescription(ctx)  ← 依赖 synthesis
  _therapy(ctx)       ← 依赖 synthesis
```

**特点**：
- 模块在编译时确定，无动态选择
- 模块间依赖通过 `await` 显式传递
- 无重规划：某模块失败后不回退重试
- 无状态恢复：中间结果不持久化到检查点
- 人工审核是同步阻断点：`POST /api/ai-outputs/{oid}/feedback`

### 各模块实现

| 模块 | 模型 | 输入 | 输出 |
|---|---|---|---|
| `_western` | clinical_main (7870) | chief_complaint+labs+history | 疾病列表+信心度 |
| `_tcm` | tcm (7871) | 同上+舌象 | 证型+治法+方药 |
| `_tongue` | clinical_main VLM (7870) | 压缩舌象图 | 舌质/舌苔/特征 |
| `_knowledge` | embedding+reranker (7873/7874) | 查询文本 | 知识条目 |
| `_synthesis` | clinical_main (7870) | 上述全部 | 综合分析 |
| `_prescription` | clinical_main (7870) | 诊断+画像 | 西药+中药+安全提示 |
| `_therapy` | clinical_main (7870) | 诊断 | 理疗方案 |

### Agent 安全边界（已满足）

1. **处方不能绕过医生**：
   - 处方草稿状态链：`AI_GENERATED → DOCTOR_CONFIRMED → SIGNED → SUBMITTED_TO_HIS`
   - 每步需显式 API 调用 + audit_log
   - `confirm`/`sign`/`submit_his` 端点均需 `doctor_id` 参数

2. **HIS 提交不能绕过签字**：
   - `submit_to_his` 检查 `status == "SIGNED"`，否则拒绝
   - MockHIS 适配器接收处方 JSON 但标记为"演示级"

3. **诊断审核阻断**：
   - `POST /api/ai-outputs/{oid}/feedback` 记录 accept/modify/reject
   - accept/modify 才创建 DiagnosisRecord
   - 只有 accepted 状态的诊断才能触发处方生成

## 二、LangGraph 评估

### LangGraph 适合性分析

| 维度 | 当前 Orchestrator | LangGraph 优势 | 当前需求 |
|---|---|---|---|
| 动态模块选择 | 固定 7 模块 | 条件分支/循环 | 低 — 模块固定 |
| 工具选择 | 无工具调用 | tool_use 节点 | 低 — 无外部工具 |
| 重规划 | 无失败重试 | 条件边回退 | 中 — 可提升健壮性 |
| 状态恢复 | 无检查点 | checkpoint 持久化 | 中 — 长流程价值高 |
| 人工审核 | 同步阻断 | interrupt+resume | 高 — 值得迁移 |
| 流式输出 | 全量返回 | 流式 token | 低 — UX 可接受 |

### 迁移成本

- **依赖**：`langgraph` 未安装，需 `pip install langgraph`（违反"不换环境"约束）
- **重构**：7 个模块需包装为 LangGraph node
- **测试**：需重新验证 E2E
- **风险**：状态管理从显式 `await` 变为隐式图遍历，调试复杂度增加

### 最小验证方案（不全面迁移）

用 LangGraph 包裹现有 `_western/_tcm/_tongue` 作为 tool nodes，对比输出：

```python
# 伪代码（仅评估，不实际实现）
from langgraph.graph import StateGraph

graph = StateGraph(ClinicalState)
graph.add_node("western", western_node)  # 调用现有 _western
graph.add_node("tcm", tcm_node)          # 调用现有 _tcm
graph.add_node("review", human_review)   # interrupt 节点
graph.add_edge("western", "tcm")
graph.add_conditional_edges("tcm", should_review)  # 高风险→review
graph.add_edge("review", "prescription")
```

**验证点**：
1. 同一病例输出是否一致
2. interrupt+resume 是否比当前同步审核更流畅
3. 检查点恢复是否在崩溃后有用

### 结论

**推荐：暂不迁移 LangGraph。**

理由：
1. **约束限制**：不换 Anaconda 环境，`langgraph` 未安装且安装可能引入依赖冲突
2. **当前架构已满足**：7 模块固定流水线 + 同步人工审核已覆盖所有临床场景
3. **成本/收益不匹配**：LangGraph 的核心优势（动态规划/状态恢复）在固定流水线场景价值有限
4. **安全边界已满足**：处方/HIS 不能绕过医生，当前状态链已保证

**未来迁移路径（如推荐）**：
1. 在新隔离环境中安装 langgraph
2. 先包裹 `_western + _tcm` 两节点验证输出一致性
3. 逐步加入 `interrupt` 节点替代同步审核
4. 保留当前 Orchestrator 作为 fallback

## 三、分模块计时（V1.2 §7）

### 实现

- `orchestrator.run_module` wrapper：记录 `t_start = time.perf_counter()` / `t_end`，计算 `duration_ms`
- `ai_runs.module_timings_json`：JSON 列 `{module: {start_ms, end_ms, duration_ms}}`
- `GET /api/ai-runs/{rid}` 返回 `module_timings` 字段
- 前端 AnalyzePanel 状态条显示每模块耗时

### 计时维度

```json
{
  "western": {"start_ms": 100, "end_ms": 3500, "duration_ms": 3400},
  "tcm": {"start_ms": 100, "end_ms": 2800, "duration_ms": 2700},
  "tongue": {"start_ms": 100, "end_ms": 900, "duration_ms": 800},
  "synthesis": {"start_ms": 3500, "end_ms": 5000, "duration_ms": 1500},
  "prescription": {"start_ms": 5000, "end_ms": 7000, "duration_ms": 2000}
}
```

## 四、总结

| 领域 | 状态 |
|---|---|
| Orchestrator 审计 | ✅ 完成 |
| 分模块计时 | ✅ DB 持久化 + 前端展示 |
| Agent 安全边界 | ✅ 处方/HIS 不能绕过医生 |
| LangGraph 评估 | ✅ 评估文档完成，推荐暂不迁移 |
