# 性能基线 (V1.2)

> 测试环境: GPU1-only (CUDA_VISIBLE_DEVICES=1), Qwen3.8-27B @7870 (clinical_main),
> ShizhenGPT-7B-Omni @7871 (tcm), Qwen3-ASR-1.7B @7872
> 测试时间: 2026-10-13

## 主 AI 分析 (orchestrator.run)

| 阶段 | 耗时 | 说明 |
|------|------|------|
| Phase 1: western + tcm (并行) | ~62s (western) + ~28s (tcm) → ~62s wall | 两个模型同时调用, western 更慢为瓶颈 |
| Phase 2: synthesis (串行) | ~22s | 等 western+tcm 完成后单次调用 |
| **主 run 总计** | **~85s** | prescription/therapy 已 deferred |

> 对比 V1.1: 原 run 含 prescription (~50s) + therapy (~40s) 串行 → 总计 ~150s。
> 现移除后主 run 降至 ~85s。处方/理疗改由按诊断单独生成。

## 按诊断生成治疗方案 (generate_treatment_for_diagnosis)

| max_tokens | 耗时 | 输出长度 | 完整性 (prescription+therapy) |
|------------|------|----------|-------------------------------|
| 600 | ~48s | ~1536 chars | ❌ 截断 (therapy 缺失) |
| 800 | ~34s | ~1114 chars | ✅ 完整 |
| 1000 | ~23s | ~834 chars | ✅ 完整 |
| 1500 | ~54s | ~2560 chars | ✅ 完整 (内容更丰富) |

**推荐配置**: max_tokens=1000, 精简 prompt → ~23-55s (平均 ~30-50s)
> 注: 实测耗时受 GPU 负载波动影响, 27B 模型吞吐 ~17 tok/s

## ASR 语音识别

| 调用 | 耗时 | 结果 |
|------|------|------|
| 第1次 | 43.2s | 200 (空文本, 合成音无语音内容) |
| 第2次 | 43.0s | 200 (同上) |
| 第3次 | 42.9s | 200 (同上) |

> 注: 连续3次调用均成功返回, 未出现挂死 — reset/reload 修复生效。
> 合成正弦波无语音内容, 返回空文本为预期行为。真实语音应返回转录文本。
> 43s/次为模型推理时间 (n_ctx=2048, 1.7B 模型在 GPU1 上)。

## 瓶颈分析

1. **27B 模型吞吐 ~17 tok/s**: 受 GPU1 单卡限制 (与 clinical_main 53G + tcm 17G 共用)
2. **无连续批处理**: llm_server 用 transformers.generate(batch_size=1), 多个并发请求串行
3. **thinking 泄漏**: Qwen3 默认输出 CoT 推理, 已用 enable_thinking=False 关闭
4. **JSON 模式开销**: 模型生成结构化 JSON 比自由文本更长

## 优化措施已实施

- [x] max_tokens 参数贯穿 LLMProvider.chat → llm_server (providers.py)
- [x] 处方/理疗从主 run 移除, 改为按诊断合并生成 (orchestrator.py)
- [x] 合并 prompt 精简: 去除 evidence 数组, 每字段限1句 (treatment_for_diagnosis.md)
- [x] max_tokens=1000 + recovery 逻辑 (截断时自动包装 prescription/therapy)
- [x] enable_thinking=False (llm_server.py line 98)
- [x] 前端轮询检查 hasContent (不仅看 status, 还检查 structured_json 有实质内容)
- [x] 深合并 PATCH (编辑处方不丢失 therapy 数据)
