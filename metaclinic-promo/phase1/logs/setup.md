# Phase 1 Environment Setup Log

## 1. Git Branch
- Repo: `/cephfs/yujc/HisCopilot`
- Branch: `metaclinic-phase1`

## 2. Python Backend Environment
- Created Conda env `hiscopilot` (Python 3.12): `/cephfs/yujc/anaconda3/envs/hiscopilot`
- Installed `ai-clinical-copilot/server/requirements.txt` (FastAPI 0.142.2, SQLAlchemy 2.1.3, Pydantic 2.13.5, uvicorn 0.54.0, pytest 9.2.0, etc.)
- Baseline test run (`phase1/logs/pytest_baseline.txt`): 205 tests discovered; all baseline tests passed (final run after fixes: **208 passed, 0 failed**, `phase1/logs/pytest_final.txt`).

## 3. Frontend Environment
- `cd /cephfs/yujc/HisCopilot/ai-clinical-copilot/apps/web && npm ci` (restored symlinks and executables lost during OBS transfer)
- `npx tsc --noEmit`: passed (0 errors)
- `npm run build`: Next.js 15.1.6 production build succeeded (23/23 static pages + dynamic routes)

## 4. Headless Browser & Screenshot Tooling
- Installed `puppeteer` + `puppeteer-core` in `/cephfs/yujc/metaclinic-promo/tools/`
- Installed Chrome `157.0.8103.78` at `/root/.cache/puppeteer/chrome/linux-157.0.8103.78/chrome-linux64/chrome`
- Installed missing Ubuntu 20.04 shared libs via `apt-get` (`libnspr4`, `libnss3`, `libatk1.0-0`, `libatk-bridge2.0-0`, `libxkbcommon0`, `libgbm1`, `libxdamage1`, `libatspi2.0-0`)
- Verified headless Chrome launch with `--no-sandbox`

## 5. Model & Proxy Configuration
- DeepSeek (`deepseek-flash` at `https://api.deepseek.com`) wired via `scripts/start-metaclinic-demo.sh` reading `/cephfs/yujc/HisCopilot/api_key.txt` at runtime (no key committed to repo).
- Unset `ALL_PROXY`, `HTTPS_PROXY`, `HTTP_PROXY` before starting FastAPI so `httpx` connects directly without requiring `socksio`.
- GPUs 0–3 left completely untouched (`gpu_keepalive2.py` preserved; zero GPU memory consumed by MetaClinic).
- OCR / ASR not deployed per user confirmation (2: 不需要展示).
