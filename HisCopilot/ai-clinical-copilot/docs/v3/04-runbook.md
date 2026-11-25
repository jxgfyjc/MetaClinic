# 象元 AI 诊疗 v3 — 运行手册（Runbook）

> 适用版本：v3.0.0（`server/` 后端 + `apps/web/` 前端）。本文只写**已经实际运行过**的命令；没验证过的会明确标注。

## 1. 组成与端口

| 组件 | 位置 | 默认端口 | 说明 |
|---|---|---|---|
| v3 API（FastAPI） | `server/` | **8011**（127.0.0.1） | **单进程**：SQLite + 进程内任务 worker，**不要** `--workers > 1` |
| v3 Web（Next.js 15） | `apps/web/` | **3100** | 同源 `/api/v1/*` 经 Next rewrite 转发到 API（`BACKEND_URL`） |
| 数据库 | `data/xiangyuan.db`（SQLite, WAL） | – | 旧库 `data/ai_clinical.db` **只读**，仅 `import-legacy` 读取 |
| 附件 | `data/objects_v3/` | – | 随机文件名，仅经鉴权接口访问，不直接对外 |
| 备份 | `data/backups/` | – | 迁移前自动备份、`cli backup` 手动备份 |
| 模型服务（**不属于本栈，只读探测**） | `services/model-gateway/` | 7870 主模型(Qwen 27B, GPU1) · 7871 中医(ShizhenGPT 7B, GPU1) · 7872 ASR(CPU) · 7875 OCR(CPU) | 本栈**从不**启动/停止/重配它们 |
| 旧版（仍在运行） | `services/api` + `apps/doctor-web` | 8001 / 3000（HTTPS 代理 3443） | 切换前不动 |

**绝不触碰**：ComfyUI（8188）、aivideo（8000）、GPU0、模型服务进程。脚本里已硬性拒绝把 API/Web 配到 8000/8188。

## 2. 依赖

- Python 3.12，conda 环境 `hiscopilot`：`<env>/bin/python -m pip install -r server/requirements.txt`
  （fastapi, uvicorn, SQLAlchemy 2.1, pydantic 2 / pydantic-settings, httpx, bcrypt, python-multipart, fpdf2, pillow；可选 PyAV 用于 m4a/webm 时长探测）
- Node 20+：`cd apps/web && npm ci`（Next 15.1.6 / React 19 / SWR / Tailwind 3 / lucide-react / qrcode）
- 中文 PDF 字体：`assets/fonts/cn-regular.otf`、`cn-medium.otf`（仓库自带）
- 模型：沿用既有的 7870–7875 服务；v3 通过 `.env` 中已有的 `QWEN_BASE_URL / SHIZHEN_BASE_URL / ASR_BASE_URL / OCR_BASE_URL` 访问（默认值见 `server/config.py`）。

## 3. 配置

v3 读取仓库根目录 `.env`（与旧版共用，模型地址、`PUBLIC_BASE_URL` 沿用），新增键以 `XY_` 开头；也可用环境变量覆盖。脚本默认值见 `scripts/v3/common.sh`，本机覆盖放 `scripts/v3/local.env`。

| 变量 | 默认 | 含义 |
|---|---|---|
| `XY_DATA_DIR` | `<repo>/data` | 数据目录（DB、附件、备份） |
| `XY_AI_MODE` | `live` | `live` 真实模型；`fake` 仅自动化测试（返回带标记的固定内容，**不得用于生产**） |
| `XY_JOBS_ENABLED` | `true` | 是否启动后台任务 worker |
| `XY_SEED_DEMO_USERS` | 开发 `true`，**脚本里生产默认 `false`** | 是否创建演示账号 |
| `XY_DEMO_PASSWORD` | `demo123` | 演示账号初始密码（首次登录强制改密） |
| `XY_COOKIE_SECURE` | `auto` | `auto`：经 HTTPS（`X-Forwarded-Proto`）时设 Secure；`true`/`false` 强制 |
| `PUBLIC_BASE_URL` | 空 | 生成患者二维码链接的对外地址，如 `https://clinic.example` |
| `XY_CLINIC_NAME` | 象元 AI 诊疗 | 患者端抬头 |
| `XY_STYLE_EXT_BASE_URL` / `XY_STYLE_EXT_MODEL` / `XY_STYLE_EXT_API_KEY` / `XY_STYLE_EXT_EXTRA_JSON` | 空 | **可选**：医师经验画像的外部模型抽取端点（默认不用；还需管理员在“画像授权与外发”页开启，且每批次医师书面确认）。密钥仅此处，不入库。详见 `06-style-profiles.md` |
| `APP_ENV` | `development` | `production` 时关闭调试行为 |

## 4. 首次部署

```bash
cd ai-clinical-copilot
# 1) 建表/迁移（有数据时会先自动在线备份到 data/backups/）
<env>/bin/python -m server.cli migrate

# 2)（如需沿用旧数据）一次性导入旧库；只读旧库、可重复执行、不覆盖已导入的行
<env>/bin/python -m server.cli import-legacy --dry-run      # 先看计划
<env>/bin/python -m server.cli import-legacy

# 3) 创建真实账号（首次登录强制改密；生产环境不要启用演示账号）
<env>/bin/python -m server.cli create-user admin ADMIN --name 管理员 --password '<临时强密码>'
<env>/bin/python -m server.cli create-user dr-xxx DOCTOR --name 张医生 --title 主治医师 --dept 中医科 --password '<临时强密码>'
#   角色：ADMIN / DOCTOR / NURSE / PHARMACIST；密码≥8位且同时含字母和数字

# 4) 构建并启动
scripts/v3/build.sh          # next build（改了 apps/web、或改了 API 地址/端口后需重跑——Next 在构建时就把 /api 代理目标写死了）
scripts/v3/start.sh          # 先 migrate，再起 API(8011)，再起 Web(3100)
scripts/v3/health.sh         # 健康检查（见 §6）
```

导入旧数据的口径与限制见《重构日志》§迁移。旧账号携带原 bcrypt 哈希导入；仍是已知默认口令（如 `demo123`）的账号会被标记为"必须改密"。

## 5. 日常操作

```bash
scripts/v3/start.sh [api|web|all]     # 重复执行安全（已运行则跳过）
scripts/v3/stop.sh  [api|web|all]     # 只停本栈自己的进程（pidfile + 命令行校验），绝不 pkill
scripts/v3/health.sh                  # 退出码 0 = 诊所需要的一切正常
<env>/bin/python -m server.cli backup                 # 一致性在线备份 → data/backups/
<env>/bin/python -m server.cli verify-audit           # 审计日志哈希链校验
<env>/bin/python -m server.cli reset-password dr-xxx --password '<新临时密码>'   # 同时撤销其全部登录
<env>/bin/python -m server.cli drain-jobs             # 维护/排错：在当前进程里把排队任务跑完
```

日志：`logs/api.log`、`logs/web.log`；每个请求有 `request_id`，错误响应与日志可互相对照。
任务状态可在管理员 → "运行状态" 页查看；失败任务含错误码和原因，医师在诊疗室里对失败的语音/OCR/分析/方案都有"重试"按钮。

### 回滚 / 恢复
```bash
scripts/v3/stop.sh api
<env>/bin/python -m server.cli restore data/backups/<备份文件>.db     # 用备份替换当前库（先停 API！）
scripts/v3/start.sh api
```
迁移前备份形如 `xiangyuan-pre-v0002-<时间>.db`。旧库 `ai_clinical.db` 从不被 v3 写入，所以回到旧版只需继续使用旧服务（8001/3000）。

## 6. 健康检查说明（`scripts/v3/health.sh`）

检查：API `/healthz`；Web `/login`；Web→API 代理**确实指向本 API 实例**（`/healthz` 与经 Web 的 `/api/v1/ping` 返回同一个 `instance` 标识——生产构建曾因代理目标在构建时写死而悄悄指向了另一个后端，`start.sh` 现在会在构建目标与当前 API 地址不一致时拒绝启动）；DB 完整性（`integrity_check`、`foreign_key_check`、迁移版本、任务状态分布、疑似卡住的任务）；审计链；四个模型服务 `/health`（只读探测，失败时 AI 功能降级为"失败 + 可重试"，不影响登记/开方/病历）；并只读确认 8000/8188 未被占用或误动。

## 7. 手机 / 语音（HTTPS）

手机浏览器在**非加密的 http 页面**不允许调用麦克风（`navigator.mediaDevices` 不存在）。v3 的处理：
- HTTPS 或 localhost：患者端直接录音（带音量条、时长、权限被拒/无设备提示）。
- HTTP：自动改为"调用手机自带录音机 / 选择已有音频文件"，并向患者说明原因——**不会显示一个点了没反应的麦克风按钮**。
- 现场要直接录音：把 `PUBLIC_BASE_URL` 设为 https 地址（旧版的 `apps/doctor-web/https-proxy.js` + `cert/` 的 3443 代理可复用，把目标改成 3100）。未验证：在真实手机上的 HTTPS 录音体验（本次只在带虚拟麦克风的桌面 Chrome 中验证了 MediaRecorder 路径）。

## 8. 故障速查

| 现象 | 原因 / 处理 |
|---|---|
| 登录提示"请先修改密码" | 账号被标记 `must_change_password`（首次/被重置/旧默认口令）。登录后进入"我的账号"改密；服务端会拒绝一切其他接口直到改完 |
| 语音一直"排队中" | ASR 服务（7872）不可用或忙：看 `health.sh`；任务会重试并最终显示失败原因，可点"重试处理" |
| OCR 很慢（10–20 秒） | CPU 版 PaddleOCR-VL 的正常耗时；任务异步，页面显示进度 |
| AI 分析失败 | 看失败卡片里的错误码/原因：`insufficient_facts`（资料太少）、`model_*`（模型服务）；可重试，也可跳过 AI 手工添加诊断 |
| `409 conflict / stale_version` | 他人/另一窗口/后台任务刚更新了同一对象：界面会提示刷新；编辑中的内容不会被覆盖（接诊资料页有逐字段冲突处理） |
| API 起不来、端口占用 | `stop.sh api`；确认没有手工起的残留（`ps` 找 `server.app:create_app`，**按 PID** 停，勿 pkill） |
| `start.sh` 报“production build proxies /api to … but the API is configured at …” | 修改过 `API_HOST/API_PORT`：重新运行 `scripts/v3/build.sh`（代理目标在构建时固定） |
| 数据库锁 | WAL + 30 秒 busy_timeout；若出现，检查是否有第二个 API 进程在写同一个 DB |

## 9. 自动化验证（开发/交付前）

```bash
<env>/bin/python -m pytest                       # 后端：领域、安全规则、权限、任务、迁移、HTTP、并发回归
tests/e2e/run_e2e.sh                             # 浏览器端到端（无头 Chrome）：专用 API:8012(fake AI) + Web:3101，数据在 /tmp/xy_e2e，不影响主栈
cd apps/web && npx tsc --noEmit                  # 类型检查
```
本手册的 `build / start / health / stop` 脚本已在一次性端口（8013/3103、临时数据目录）上完整演练过：构建、启动（含迁移）、重复启动为空操作、生产模式下无演示账号、CLI 建账号、经 Web 代理登录并被强制改密、改密后业务接口可用、健康检查全绿、停止后端口关闭且不残留进程。

`run_e2e.sh` 只停自己的 8012 进程（精确命令行匹配），可用 `KEEP=1` 复用现有栈、传入单个场景文件名只跑该场景。
