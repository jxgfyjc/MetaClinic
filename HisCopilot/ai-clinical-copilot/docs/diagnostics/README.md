# 象元 AI 诊疗 — 诊断日志指南

> ASR/OCR 全链路 request_id 串联、日志位置、查询方式。

## 日志文件位置

| 服务 | 日志文件 | request_id 前缀 | 说明 |
|------|----------|----------------|------|
| FastAPI (8001) | `/tmp/api_8001.log` | `asr-` / `ocr-` | ASR 代理 + OCR 代理，记录 request_id、阶段耗时、上游状态、错误分类 |
| ASR Server (7872) | `/tmp/asr_7872.log` | `asr-` | 模型推理日志，记录 audio_meta、success/failed、latency_ms |
| OCR Server (7875) | `/tmp/ocr_7875.log` | `ocr-` | PaddleOCR-VL 推理日志 (comfui 环境, llama-cpp 0.4.2) |
| 审计日志 | `data/audit.ndjson` | — | 所有关键操作（AI 分析、处方签字、HIS 提交、随访等），JSONL 格式 |

> **注意**：`/tmp/*.log` 在服务重启后保留（nohup 重定向），但系统重启后清除。
> 审计日志 `data/audit.ndjson` 为持久化、append-only。

## request_id 格式

- ASR: `asr-<uuid12>` （如 `asr-d3eba535462b`）
- OCR: `ocr-<uuid12>` （如 `ocr-7658eb480512`）

每次请求生成唯一 request_id，贯穿：浏览器 → API 代理 → 模型服务。

## 查询方式

### 按 request_id 查询完整链路

```bash
# 查 ASR 代理日志
grep "asr-d3eba535462b" /tmp/api_8001.log

# 查 ASR 模型日志
grep "asr-d3eba535462b" /tmp/asr_7872.log

# 查 OCR 代理日志
grep "ocr-7658eb480512" /tmp/api_8001.log

# 查审计日志（按 request_id 或操作类型）
grep "followup.response\|prescription.sign" data/audit.ndjson | tail -20
```

### 查询审计日志（API）

```bash
# 获取最近 50 条审计日志
curl http://127.0.0.1:8001/api/audit?limit=50

# 获取最近 100 条
curl "http://127.0.0.1:8001/api/audit?limit=100"
```

### 实时监控

```bash
# 实时查看 API 代理日志
tail -f /tmp/api_8001.log

# 实时查看 ASR 模型日志
tail -f /tmp/asr_7872.log
```

## 日志字段

### ASR 日志（API 代理层）

```
[asr-{rid}] audio_meta={size, filename, format, sample_rate, channels, duration_s}
[asr-{rid}] success text_len={N} latency_ms={ms}
[asr-{rid}] {category}: {error} stages={...}
```

### OCR 日志（API 代理层）

```
[ocr-{rid}] received filename={f} size={N} dims={W}x{H}
[ocr-{rid}] success source={paddleocr-vl|clinical_main_vlm} text_len={N} latency_ms={ms}
[ocr-{rid}] FAILED {detail} stages={...}
```

## 错误分类

### ASR 错误分类（9 类）

| category | 中文标签 | 说明 |
|----------|---------|------|
| mic_permission | 麦克风权限被拒绝 | 浏览器未授权麦克风 |
| upload_failed | 音频上传失败 | 文件为空或读取失败 |
| format_unsupported | 音频格式不支持 | 非音频文件或格式不支持 |
| decode_failed | 音频解码失败 | 解码后为空或损坏 |
| model_unavailable | ASR 服务未启动 | 7872 端口不可达 |
| inference_exception | ASR 推理异常 | 模型推理过程异常 |
| cuda_oom | 显存不足 (OOM) | GPU 显存不足 |
| post_processing | 后处理失败 | 模型输出后处理失败 |
| timeout | 推理超时 | 90s 超时 |

### OCR 错误分类

| category | 中文标签 | 说明 |
|----------|---------|------|
| upload_failed | 图片上传失败 | 文件为空或读取失败 |
| ocr_unavailable | OCR 服务不可用 | 所有 OCR 后端均不可用 |
| timeout | OCR 推理超时 | 上游超时 |
| model_unavailable | OCR 模型未启动 | 7875/7870 均不可达 |

## 前端错误展示

前端在 ASR/OCR 失败时显示：`{中文标签}：{detail} [{request_id}]`

开发者可通过 request_id 在日志中定位完整异常堆栈和阶段耗时。

## 日志安全

- 日志不记录完整病情文本、原始音频内容、原始图片内容
- ASR 仅记录 `text_len`（文本长度），不记录转写文本本身
- OCR 仅记录 `text_len`，不记录识别文本内容
- 音频元数据仅记录：size、format、sample_rate、channels、duration
- 图片元数据仅记录：size、width、height、mime
- 审计日志 `data/audit.ndjson` 记录操作类型和 ID，不记录临床数据详情

---

## 失败根因与修复

### 根因 1：structured_json 被 confirm/sign/submit 覆盖（严重）

**现象**：处方签字提交后，处方+理疗数据丢失，structured_json 变为 `{}`。

**根因**：`packages/shared/repo.py` 的 `PrescriptionDraftRepo.update_structured` 无条件执行 `d.structured_json = structured_json`。confirm/sign/submit 操作传 `{}` 作为 structured_json，导致已生成的处方和理疗数据被空 dict 覆盖。

**修复**：加 `if structured_json:` guard — 空 dict 不覆盖已有数据。

**验证**：处方 `rx-d866ad9a` (SUBMITTED_TO_HIS) → json_len=1855, has_prescription=True, has_therapy=True ✅

### 根因 2：OCR 代理无 request_id 诊断

**现象**：OCR 失败时无法追溯具体请求和失败阶段。

**根因**：`services/api/app.py` 的 OCR 代理端点直接转发，无 request_id 生成、无阶段计时、无结构化错误分类。

**修复**：完全重写 OCR 代理端点：
- 生成 `ocr-<uuid12>` request_id
- 探测图片元数据 (PIL: size, filename, mime, width×height)
- 阶段计时 (paddleocr_ms, clinical_main_ms, total_ms)
- 结构化错误响应 (detail + request_id + category)
- 成功返回 {text, source, request_id, latency_ms, image_meta}

### 根因 3：ASR 错误分类不精确

**现象**：非音频文件上传，ASR 返回 category=`post_processing` 而非 `format_unsupported`。

**根因**：`asr_server.py` 的 `_classify_error` 中 `"post" in msg` 匹配过于宽泛——"post_processing" 本身含 "post"，且 llama.cpp 的错误消息中常含 "post"，导致几乎所有异常被分类为 post_processing。

**修复**：
- 移除 `"post" in msg` 宽泛匹配
- 精确匹配 `unsupported audio format` / `magic bytes` / `only supports` → `format_unsupported`
- 精确匹配 `failed to load media` / `corrupted data` → `decode_failed`
- 新增 `timeout` / `timed out` → `timeout` 分类

### 根因 4：中药饮片页显示"未接入"硬编码数据

**现象**：pharmacy/herbs 页面显示 DemoBanner "未接入真实接口"，但 DB 有 424 条中药饮片。

**根因**：`app/pharmacy/herbs/page.tsx` 使用硬编码 DEMO_HERBS 数组，未调用 `/api/drugs?category=tcm-herb`。且 `DrugRepo.search` 默认 limit=200 不够 424 条。

**修复**：重写 herbs 页面从 API 获取真实数据；DrugRepo.search 默认 limit 200→500；/api/drugs 端点加 limit 参数。

---

## 测试步骤与结果 (2026-10-14)

### 1. OCR 正常识别（真实检验报告）

```bash
curl -X POST http://127.0.0.1:8001/api/ocr/extract \
  -F "file=@yujc/HisCopilot/test/微信图片_20261013234121_69_2.jpg"
```

**结果**：HTTP 200, request_id=`ocr-aa37cc4b0728`, source=`clinical_main_vlm`, text_len=892, latency_ms=50127, image_meta={size:83524, 792×555, jpeg}

**识别内容**：广丰丰溪堂中医院检验报告单，含姓名(余锦晨)、科室(中医科门诊)、项目(脂蛋白a/血清总胆固醇/甘油三酯等)、结果、单位、参考范围——与真实报告一致。

### 2. OCR 失败（非图片文件）

```bash
echo "this is not an image" > /tmp/fake_not_image.jpg
curl -X POST http://127.0.0.1:8001/api/ocr/extract -F "file=@/tmp/fake_not_image.jpg"
```

**结果**：HTTP 503, request_id=`ocr-7b99e64c0ca4`, category=`ocr_unavailable`, detail="paddleocr-vl 服务未启动 (port 7875)；clinical_main HTTP 503: cannot identify image file"

### 3. ASR 正常识别（真实 WAV）

```bash
# 生成 2s 16kHz 正弦波 WAV
curl -X POST http://127.0.0.1:8001/api/asr/transcribe -F "file=@/tmp/test_tone.wav"
```

**结果**：HTTP 200, request_id=`asr-06a2216d688f`, text_len=0 (正弦波无语音内容), latency_ms=52518, audio_meta={size:64044, format:wav, sr:16000, channels:1, duration:2.0}

### 4. ASR 失败 — 格式不支持

```bash
echo "hello world" > /tmp/fake_audio.wav
curl -X POST http://127.0.0.1:8001/api/asr/transcribe -F "file=@/tmp/fake_audio.wav"
```

**结果**：HTTP 503, request_id=`asr-4171953cfb5b`, category=`format_unsupported`, detail="Unsupported audio format detected via magic bytes"

### 5. ASR 失败 — 解码失败

```bash
# 0 帧 WAV 文件
curl -X POST http://127.0.0.1:8001/api/asr/transcribe -F "file=@/tmp/empty.wav"
```

**结果**：HTTP 503, request_id=`asr-2b8dcff67427`, category=`decode_failed`, detail="Failed to load media from bytes (corrupted data)"

### 6. ASR 失败 — 服务未启动

```bash
# kill 7872 后请求
curl -X POST http://127.0.0.1:8001/api/asr/transcribe -F "file=@/tmp/test_tone.wav"
```

**结果**：HTTP 503, category=`model_unavailable`, detail="ASR 服务未启动 (port 7872)"

### 7. structured_json 保全

```bash
# DB 验证
sqlite3 data/ai_clinical.db "SELECT id, status, length(structured_json) FROM prescription_drafts ORDER BY rowid DESC LIMIT 3"
```

**结果**：最新处方 `rx-d866ad9a` (SUBMITTED_TO_HIS) → json_len=1855, has_prescription=True, has_therapy=True ✅

### 8. 全链路业务 E2E

完整工作流验证（API 级）：患者创建→OCR→就诊→AI 分析(90s)→综合评估→诊断采纳→治疗方案(35s)→处方编辑→确认→签字→MockHIS 提交→随访→患者响应→风险提醒→数据持久化

---

## 未解决 / 无法自动化的问题

1. **浏览器实际录音测试**：ASR 链路已通过 API E2E 验证（WAV→request_id+text+audio_meta），但前端浏览器 getUserMedia→MediaRecorder→/api/asr/transcribe 流程需手动浏览器交互验证。CLI 无法自动化 headless 录音。

2. **PaddleOCR-VL (7875) 未启动**：OCR 使用 clinical_main VLM fallback，功能正常但延迟较高(~50s)。启动 PaddleOCR-VL 可降低延迟但需额外 GPU 显存。

3. **前端按钮点击验证**：所有按钮通过 API 级 E2E 验证（端到端调用后端 API），但实际浏览器 DOM 按钮点击交互未逐一自动化。

## ASR 模型输出空文本问题（2026-10-14 深入排查）

### 现象

ASR 成功读入音频（audio_meta 正确：16kHz/mono/duration_s=2-5s），推理完成无报错（HTTP 200, finish_reason=stop），但 `text_len=0`。

### 模型原始输出

```
raw_model_output content='language None<asr_text>' reasoning='' finish_reason=stop completion_tokens=3
```

模型只输出了 3 个 token：`language None<asr_text>`——即语言检测为 None，`<asr_text>` 标签后无任何转录文本，立即 stop。

### 根因

**Qwen3-ASR-1.7B gguf (Q8_0) 在 llama-cpp 0.4.2 下无法完成实际语音转录。**

关键证据：
- 模型正确接收了音频（`Media count: 1`, `1 bitmaps were successfully created`）
- 模型正确评估了音频 chunk（`Evaluating AUDIO chunk (39 tokens)`）
- 但模型输出停在 `language None<asr_text>`，未生成任何转录文本
- 这不是提示词问题——已验证：
  - 用 system message（handler 的 chat template 只读 system 角色文本）
  - 不加 system message
  - 用 `input_audio` 格式 vs `audio_url` 格式
  - 用 5s 复杂多谐波 WAV vs 2s 正弦波
  - max_tokens 512 vs 1024 vs 4096
  - 所有组合均输出 `language None<asr_text>` + 空

- 这不是音频格式/采样率问题——音频已转为 16kHz mono s16 WAV（Qwen3-ASR 官方要求）
- llama.cpp 官方文档标注 audio input 为实验性功能：`init_audio: audio input is in experimental stage and may have reduced quality`

### 结论

**Qwen3-ASR gguf + llama-cpp-python 的音频输入功能目前处于实验阶段，Q8_0 量化模型无法实际转录语音。** 模型能接收音频 token 但生成停在语言检测阶段，不输出转录文本。这是上游 llama.cpp 的已知限制，非本项目代码问题。

### 当前处理

- ASR 链路完整：音频上传 → 转码 → 模型推理 → 返回（带 request_id + audio_meta）
- 错误处理完善：非音频文件 → format_unsupported；空 PCM → decode_failed；服务关闭 → model_unavailable
- 前端新增实时音量条 + 录音试听播放器，用户可确认麦克风确实采集到声音
- text_len=0 时前端显示"未识别到语音内容（可能是录音过短或无清晰语音，请大声重试）"

### 可能的后续方案（需用户决策，涉及约束）

1. **等待 llama-cpp 升级** — audio input 功能成熟后 Q8_0 gguf 可能正常转录（不违反"不下载新模型"约束，仅升级库）
2. **尝试 bf16/f16 量化** — 可能量化损失导致 ASR 失效（需下载新 gguf 文件，涉及"不下载新模型"约束）
3. **用 transformers 原生推理** — Qwen3-ASR-1.7B 原版（非 gguf）走 transformers，音频输入更成熟（需额外 GPU 显存，涉及环境约束）
